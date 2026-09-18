import sys
# import json
from PyQt5 import QtWidgets as qtw
from PyQt5 import QtCore as qtc
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QDesktopWidget
from PyQt5.QtCore import QMutex, QMutexLocker

# import vlc
import board
import busio
from digitalio import Direction, Pull
from RPi import GPIO
from adafruit_mcp230xx.mcp23017 import MCP23017

from model import Model

class MainWindow(qtw.QMainWindow): 
    # Most of this module is analogous to svelte Panel

    # These signals are internal to control.py
    startPressed = qtc.pyqtSignal()
    plugEventDetected = qtc.pyqtSignal()
    plugInToHandle = qtc.pyqtSignal(int)
    unPlugToHandle = qtc.pyqtSignal(int)
    dualUnplugToHandle = qtc.pyqtSignal(int, int)  # pin1, pin2

    # NEW: Thread-safe signal for GPIO interrupts
    gpioInterruptSignal = qtc.pyqtSignal(list, int)  # interrupt flags, plus a snapshot of all pins
    
    awaitingRestart = False
    interrupt = 17

    def __init__(self):
        # self.pygame.init()
        super().__init__()

        # ------- pyqt window ----
        self.setWindowTitle("You Are the Operator")
        self.label = qtw.QLabel(self)
        self.label.setWordWrap(True)
        # self.label.setText("Keep your ears open for incoming calls! ")

        self.label.setAlignment(qtc.Qt.AlignTop)
        # Set margins using stylesheet
        self.label.setStyleSheet("""
            QLabel {
                margin-left: 30px;
                margin-top: 20px;
            }
        """)
        # padding: 10px;
        # Large text
        self.label.setFont(QFont('Arial',30))


        # Get screen dimensions
        screen = QDesktopWidget().screenGeometry()
        screen_width = screen.width()
        screen_height = screen.height()
        
        # Calculate position and size based on percentages
        # width = int(screen_width * 0.8)  # 80% of screen width
        height = int(screen_height * 0.3)  # 60% of screen height
        # x = int((screen_width - width) / 2)  # Center horizontally
        # y = int((screen_height - height) / 2)  # Center vertically
        y = int(screen_height - height)  # Center vertically
        
        # Apply geometry
        self.setGeometry(0, y, screen_width, height)

        # # Small text for debug
        # self.label.setFont(QFont('Arial',16))
        # self.setGeometry(15,80,600,250)

        self.setCentralWidget(self.label)
        self.model = Model()

        # --- timers --- 
        self.bounceTimer=qtc.QTimer()
        self.bounceTimer.timeout.connect(self.continueCheckPin)
        self.bounceTimer.setSingleShot(True)
        self.blinkTimer=qtc.QTimer()
        self.blinkTimer.timeout.connect(self.blinker)

        self.captionTimer=qtc.QTimer()
        self.captionTimer.setSingleShot(True)
        self.captionTimer.timeout.connect(self.display_next_caption)
        self.captionIndex = 0
        self.captions = 'empty'
        self.areCaptionsContinuing = True

        # === MISUSE DETECTION ===
        # Track plug-ins to detect rapid/chaotic usage
        self.plugin_history = []  # List of (time, pin) tuples
        self.MISUSE_THRESHOLD = 4  # Number of plug-ins
        self.MISUSE_WINDOW = 12000  # 12 seconds in milliseconds
        
        # Timer to periodically clean old plug-in history
        self.cleanupTimer = qtc.QTimer()
        self.cleanupTimer.timeout.connect(self.cleanupPluginHistory)
        self.cleanupTimer.start(5000)  # Clean every 5 seconds

        # Self (control) for gpio related, self.model for audio
        self.startPressed.connect(self.startSim)

        # Bounce timer less than 200 cause failure to detect 2nd line
        # Tested with 100
        self.plugEventDetected.connect(lambda: self.bounceTimer.start(300))
        self.plugInToHandle.connect(self.model.handlePlugIn)
        self.unPlugToHandle.connect(self.model.handleUnPlug)
        
        # NEW: Connect the thread-safe GPIO signal
        self.gpioInterruptSignal.connect(self.handleGpioInterrupt)

        # Events from model.py
        self.model.displayTextSignal.connect(self.displayText)
        self.model.setLEDSignal.connect(self.setLED)
        # self.model.pinInEvent.connect(self.setPinsIn)
        self.model.blinkerStart.connect(self.startBlinker)
        self.model.blinkerStop.connect(self.stopBlinker)
        # self.model.checkPinsInEvent.connect(self.checkPinsIn)
        self.model.displayCaptionSignal.connect(self.displayCaptions)
        self.model.stopCaptionSignal.connect(self.stopCaptions)
        self.model.stopSimSignal.connect(self.stopSim)
        self.dualUnplugToHandle.connect(self.model.handleDualUnplug)
   
        # Add tracking for pending interrupts
        self.pending_interrupts = []  # Track interrupts that haven't been processed yet
        self.interrupt_lock = qtc.QMutex()  # Thread safety for pending_interrupts

        # Jacks waiting to be checked after the bounce delay, in the order
        # their interrupts arrived. A single pinFlag used to be overwritten
        # when two plugs went in at once, so the first one was never handled.
        self.pinsToCheck = []

        # What the CHIP says is plugged in, which is not the same thing as
        # model.pinsIn -- that records the plugs the game logic accepted. A
        # jack rejected as the wrong number is physically in but not in the
        # model, and comparing against the model would rediscover it forever.
        self.pinsPhysical = [False] * 16
        # Jacks we re-offered after a caller was accepted; they are not new
        # plug-ins and must not count toward the misuse catcher
        self.reofferedPins = set()
        self.callerWasPlugged = False

        # Enhanced tracking for dual-unplug detection
        self.unplug_history = []  # Track all unplugs with timestamps
        self.last_unplug_time = None
        self.last_unplug_pin = -1

        # Initialize the I2C bus:
        i2c = busio.I2C(board.SCL, board.SDA)
        self.mcp = MCP23017(i2c) # default address-0x20
        # self.mcpRing = MCP23017(i2c, address=0x22)
        self.mcpLed = MCP23017(i2c, address=0x21)

        # -- Make a list of pins for each bonnet, set input/output --
        # Plug tip, which will trigger interrupts
        self.pins = []
        for pinIndex in range(0, 16):
            self.pins.append(self.mcp.get_pin(pinIndex))
        # Will be initiallized to pull.up in reset()

        # LEDs 
        # Tried to put these in the Model/logic module -- but seems all gpio
        # needs to be in this base/main module
        self.pinsLed = []
        for pinIndex in range(0, 12):
            self.pinsLed.append(self.mcpLed.get_pin(pinIndex))
        # Set to output in reset()

        # -- Set up Tip interrupt --
        self.mcp.interrupt_enable = 0xFFFF  # Enable Interrupts in all pins
        # self.mcp.interrupt_enable = 0xFFF  # Enable Interrupts first 12 pins
        # self.mcp.interrupt_enable = 0b0000111111111111  # Enable Interrupts in pins 0-11 aka 0xfff

        # If intcon is set to 0's we will get interrupts on both
        #  button presses and button releases
        self.mcp.interrupt_configuration = 0x0000  # interrupt on any change
        self.mcp.io_control = 0x44  # Interrupt as open drain and mirrored
        # put this in startup?

        self.mcp.clear_ints()  # Interrupts need to be cleared initially
        self.reset()

        # Instead of defining checkPin inside __init__, use:
        self.interrupt = 17  # Define as an instance variable for reuse in reset()
        GPIO.setmode(GPIO.BCM)

        # First remove any existing event detection
        try:
            GPIO.remove_event_detect(self.interrupt)
        except:
            pass  # Handle exception if no event detection exists

        GPIO.setup(self.interrupt, GPIO.IN, GPIO.PUD_UP)
        GPIO.add_event_detect(self.interrupt, GPIO.BOTH, callback=self.checkPin, bouncetime=50)

        # Edge detection is armed now. If the INT line is still held low from
        # startup settling, no edge can ever arrive and the first plug is lost,
        # so release it here. Discarding these startup flags is intended.
        stale, _ = self.readInterrupts()
        if stale:
            print(f" * startup: released interrupt line, discarding stale flags {stale}")

    def checkForMisuse(self):
        """Check if user is plugging in too rapidly"""
        current_time = qtc.QTime.currentTime()
        
        # Remove old entries outside the window
        cutoff_time = current_time.addMSecs(-self.MISUSE_WINDOW)
        self.plugin_history = [(t, p) for t, p in self.plugin_history if t > cutoff_time]
        
        # Check if we've exceeded the threshold
        if len(self.plugin_history) >= self.MISUSE_THRESHOLD:
            print(f" *** MISUSE DETECTED: {len(self.plugin_history)} plug-ins within {self.MISUSE_WINDOW/1000} seconds")
            # Stop everything
            self.handleMisuse()
            return True
        return False
    
    def handleMisuse(self):
        """Handle detected misuse by stopping simulation with message"""
        print(" * Got to handleMisuse -- stopping")
        # Clear plugin history to prevent repeated triggers
        self.plugin_history.clear()
        # Drop any jacks still waiting to be checked
        self.pinsToCheck.clear()
        
        # Display message
        self.displayText("Looks like a confusing situation.\nPress the Start button to start over -- calmly -- one thing at a time!.")
        
        # Stop the simulation
        self.stopMedia()
        
        # Log the misuse
        print(" *** Simulation stopped due to rapid plug-ins (misuse detected)")
    
    def cleanupPluginHistory(self):
        """Periodically clean old entries from plugin history"""
        if self.plugin_history:
            current_time = qtc.QTime.currentTime()
            cutoff_time = current_time.addMSecs(-self.MISUSE_WINDOW)
            old_count = len(self.plugin_history)
            self.plugin_history = [(t, p) for t, p in self.plugin_history if t > cutoff_time]
            if old_count != len(self.plugin_history):
                print(f" - Cleaned {old_count - len(self.plugin_history)} old plug-in entries")


    def checkPin(self, port):
        """GPIO interrupt callback - runs in interrupt thread.
        We must be thread-safe here, so we just gather data and emit a signal.
        """
        try:
            # Read interrupt flags and pin values in the interrupt thread
            interrupt_data, gpio_state = self.readInterrupts()
            
            # Emit signal to main thread with the data
            if interrupt_data:
                self.gpioInterruptSignal.emit(interrupt_data, gpio_state)
        except Exception as e:
            print(f"Error in GPIO interrupt handler: {e}")

    def handleGpioInterrupt(self, interrupt_data, gpio_state=-1):
        """Handle GPIO interrupts in the main thread where Qt operations are safe"""

        # The chip interrupts on change from the previous value, and that
        # baseline is reset when we read GPIO. A jack that changes between the
        # flag being set and our read gets absorbed into the baseline: its new
        # value is in the snapshot, but it never raises a flag of its own.
        # Recover those by comparing the snapshot against what the model believes.
        if gpio_state >= 0:
            self.printSnapshot(gpio_state)
            flagged = [pin for pin, _ in interrupt_data]
            interrupt_data.extend(self.reconcileFromSnapshot(gpio_state, flagged))

        current_time = qtc.QTime.currentTime()
        unplugs_detected = []
        
        # First, collect all unplugs from this interrupt batch
        for pin_flag, pin_value in interrupt_data:
            print(f"* Interrupt - pin number: {pin_flag} changed to: {pin_value}")
            
            # Check if this is an unplug (pin went high and was previously in)
            if (pin_flag < 12 and 
                pin_value == True and 
                self.model.getIsPinIn(pin_flag)):
                unplugs_detected.append(pin_flag)
        
        # Add unplugs to history
        for pin in unplugs_detected:
            self.unplug_history.append({
                'pin': pin,
                'time': current_time,
                'processed': False
            })
            # Keep only last 5 seconds of history
            five_seconds_ago = current_time.addSecs(-5)
            self.unplug_history = [u for u in self.unplug_history if u['time'] > five_seconds_ago]
        
        # Print current pin states for debugging
        if unplugs_detected:
            print(f" DEBUG: Unplugs detected: {unplugs_detected}")
            print(f" DEBUG: Current pin states (0-11): ", end="")
            for i in range(12):
                print(f"{i}:{'IN' if self.model.getIsPinIn(i) else 'OUT'} ", end="")
            print()
            print(f" DEBUG: Unplug history: {[(u['pin'], u['time'].toString('hh:mm:ss.zzz')) for u in self.unplug_history[-5:]]}")
        
        # Check for dual-unplug scenario
        dual_unplug = False
        
        # Case 1: Multiple unplugs in same interrupt batch
        if len(unplugs_detected) >= 2:
            print(f" ** DUAL-UNPLUG DETECTED (same batch): pins {unplugs_detected[0]} and {unplugs_detected[1]} unplugged together")
            dual_unplug = True
        
        # Case 2: Check against recent unplugs in history
        elif len(unplugs_detected) == 1:
            current_pin = unplugs_detected[0]
            # Look for another unplug in recent history
            for hist in reversed(self.unplug_history[:-1]):  # Skip the current one
                time_diff = hist['time'].msecsTo(current_time)
                if time_diff < 500 and hist['pin'] != current_pin:  # Within 500ms
                    print(f" ** DUAL-UNPLUG DETECTED (from history): pins {hist['pin']} and {current_pin} unplugged within {time_diff}ms")
                    dual_unplug = True
                    break
        
        # Update last unplug tracking
        if unplugs_detected:
            self.last_unplug_time = current_time
            self.last_unplug_pin = unplugs_detected[-1]
        
        # Process interrupts normally
        for pin_flag, pin_value in interrupt_data:
            # Test for phone jack vs start and stop buttons
            if pin_flag < 12:
                # Track if this interrupt is being processed or ignored
                if (pin_value == True and self.model.getIsPinIn(pin_flag)):
                    if self.just_checked:
                        print(f" * Interrupt for pin {pin_flag} (unplug) ignored due to just_checked")
                
                # Don't restart this interrupt checking if we're still
                # in the pause part of bounce checking
                if not self.just_checked:
                    # Queue this jack rather than overwriting the last one,
                    # so two plugs going in together both get handled
                    if pin_flag not in self.pinsToCheck:
                        self.pinsToCheck.append(pin_flag)
                    self.plugEventDetected.emit()
                    # Mark this unplug as being processed
                    for u in self.unplug_history:
                        if u['pin'] == pin_flag and not u['processed']:
                            u['processed'] = True
                            break

            else:
                print(" * got to interrupt 12 or greater \n")
                if pin_flag == 13 and pin_value == False:
                    self.startPressed.emit() # Calls stopMedia
                elif pin_flag == 12:
                    print(f'   * got to stop, aka pin 12, {pin_value}')
                    self.stopSim()

    def stopSim(self):
        print('stopping sim')
        self.label.setText("The Switchboard has stopped. Press the Start button to begin!")
        self.stopMedia()

    def startSim(self):
        self.stopMedia()
        if (self.getAnyPinsIn()):
            self.label.setText("Remove phone plugs and when you're ready, press Start")
        else:
            self.reset()
            self.model.handleStart()

    def stopMedia(self):
        print(" * resetting, starting")
        self.awaitingRestart = True
        self.stopCaptions()
        self.setLEDsOff()
        self.model.stopAllAudio()
        self.model.stopTimers()
        # Stop blinking
        if self.bounceTimer.isActive():
            self.bounceTimer.stop()
        if self.blinkTimer.isActive():
            self.blinkTimer.stop()            
        if self.captionTimer.isActive():
            self.captionTimer.stop()  
        if self.cleanupTimer.isActive():
            self.cleanupTimer.stop()

    def reset(self):
        # Clear interrupts
        self.mcp.clear_ints()
        
        self.label.setText("Press the Start button to begin!")
        self.just_checked = False
        self.pinFlag = 15
        self.pinToBlink = 0
        self.awaitingRestart = False
        self.captionIndex = 0

        # Set to input - later will get interrupt as well.
        # This has to come BEFORE reading them below: the MCP powers up with
        # its pull-ups off, so on a cold start the reads float and the model
        # can begin life believing a jack is already plugged in.
        for pinIndex in range(0, 16):
            self.pins[pinIndex].direction = Direction.INPUT
            self.pins[pinIndex].pull = Pull.UP

        # Synchronize pin states with model
        for pinIndex in range(0, 12):
            is_pin_in = self.pins[pinIndex].value == False
            self.model.setPinIn(pinIndex, is_pin_in)
            self.pinsPhysical[pinIndex] = is_pin_in
        self.reofferedPins.clear()
        self.callerWasPlugged = False
        print(f" * reset: jacks seen as plugged in at start: "
              f"{[p for p in range(12) if self.model.getIsPinIn(p)]}")
        
        # Set LEDs to output and off
        for pinIndex in range(0, 12):
            self.pinsLed[pinIndex].switch_to_output(value=False)

        # Call model's reset
        self.model.reset()
        # Ensure all VLC event handlers are detached
        self.model.detachAllEventHandlers()

        # Stop any active timers
        if self.bounceTimer.isActive():
            self.bounceTimer.stop()
        if self.blinkTimer.isActive():
            self.blinkTimer.stop()            
        if self.captionTimer.isActive():
            self.captionTimer.stop()  

        # Clear misuse detection state
        self.plugin_history.clear()
        self.pinsToCheck.clear()

        # Reconfigure the MCP23017 interrupt system
        self.mcp.interrupt_configuration = 0x0000  # interrupt on any change
        self.mcp.io_control = 0x44  # Interrupt as open drain and mirrored
        self.mcp.clear_ints()  # Final clear of interrupts
    
        # self.setLED(10, True)          
        # self.setLED(11, True)          

    # Modified continueCheckPin to emit signal during active calls:
    def continueCheckPin(self):
        """Modified to detect ghost unplugs and handle dual-unplugs during active calls"""
        # Not able to send param through timer, so take the next queued jack
        if self.pinsToCheck:
            self.pinFlag = self.pinsToCheck.pop(0)
        print(f" * In continue, pinFlag = {str(self.pinFlag)} "
            f"  * value: {str(self.pins[self.pinFlag].value)}"
            f"  * still queued: {self.pinsToCheck}")

        if (self.awaitingRestart):
            # Nothing is running, so take note of the jack but do nothing with
            # it. This has to come BEFORE the dual-unplug detection below --
            # that path emits to the model directly, and pulling two plugs
            # while stopped used to restart the call without a Start press,
            # leaving the sim ringing while every plug was ignored.
            print(' * awaiting restart')
            self.pinsPhysical[self.pinFlag] = (self.pins[self.pinFlag].value == False)
            self.scheduleNextCheck()
            return

        # === GHOST UNPLUG DETECTION ===
        # When we process an unplug, check if any other "IN" pins are actually unplugged
        if (self.pins[self.pinFlag].value == True and self.model.getIsPinIn(self.pinFlag)):
            # This is an unplug - check for ghost unplugs
            ghost_unplugs = []
            for i in range(12):
                if i != self.pinFlag and self.model.getIsPinIn(i):
                    # Model thinks this pin is IN, but let's check actual state
                    actual_value = self.pins[i].value
                    if actual_value == True:  # Pin is actually unplugged!
                        ghost_unplugs.append(i)
                        print(f" ** GHOST UNPLUG DETECTED: pin {i} is physically unplugged but didn't generate interrupt!")
            
            if ghost_unplugs:
                print(f" ** DUAL-UNPLUG DETECTED (with ghost): pin {self.pinFlag} interrupted, pin(s) {ghost_unplugs} silently unplugged")
                
                # Check if this is during an active call
                if self.model.phoneLine["isEngaged"]:
                    print(f" ** DUAL-UNPLUG during ACTIVE CALL - handling both pins together")
                    # Emit dual-unplug signal instead of single unplug
                    self.dualUnplugToHandle.emit(self.pinFlag, ghost_unplugs[0])
                    # Skip the normal single unplug processing
                    self.scheduleNextCheck()
                    return
        
        # Check if there's another recent unplug we should know about
        current_time = qtc.QTime.currentTime()
        for hist in reversed(self.unplug_history):
            if hist['pin'] != self.pinFlag and not hist['processed']:
                time_diff = hist['time'].msecsTo(current_time)
                if time_diff < 500:
                    print(f" ** POSSIBLE DUAL-UNPLUG: pin {hist['pin']} was unplugged {time_diff}ms ago")
        
        if (self.awaitingRestart):
            # do nothing - awaiting press of start button
            print(' * awaiting restart')
        else:
            # Plug-in
            if (self.pins[self.pinFlag].value == False):
                # grounded by tip, aka connected
                """
                False/grounded, then this event is a plug-in
                """

                # A pin the model already has in can't be plugged in again --
                # this is the plug being wiggled. Without this guard the
                # re-trigger is read as the second plug of the line, so the
                # caller gets answered as its own wrong number.
                self.pinsPhysical[self.pinFlag] = True

                if (self.model.getIsPinIn(self.pinFlag)):
                    print(f" * pin {self.pinFlag} already in - wiggle ignored")
                else:
                    # A jack we re-offered is not a new plug-in, so it must not
                    # count toward the misuse catcher
                    if self.pinFlag in self.reofferedPins:
                        self.reofferedPins.discard(self.pinFlag)
                    else:
                        # === MISUSE DETECTION - Track plug-ins ===
                        current_time = qtc.QTime.currentTime()
                        self.plugin_history.append((current_time, self.pinFlag))

                        # Check for misuse
                        if self.checkForMisuse():
                            # Misuse detected, don't process this plug-in
                            return

                    # Send pin index to model.py as an int
                    # Model uses signals for LED, text and pinsIn to set here
                    self.plugInToHandle.emit(self.pinFlag)
            # Unplug
            else: # pin flag True, still, or again, high
                # aka not connected
                # was this a legit unplug?
                self.pinsPhysical[self.pinFlag] = False

                if (self.model.getIsPinIn(self.pinFlag)):
                    # if this pin was in
                    print(f" * pin {self.pinFlag} was in - handleUnPlug")
                    # On unplug we can't tell which line electronically 
                    # (diff in shaft is gone), so rely on pinsIn info
                    self.unPlugToHandle.emit(self.pinFlag)
                    # Model handleUnPlug will set pinsIn false for this one
                else:
                    print(" ** got to pin true (changed to high), but not pin in")

        self.scheduleNextCheck()

    def scheduleNextCheck(self):
        # Delay setting just_checked to false in case the plug is wiggled
        qtc.QTimer.singleShot(150, self.delayedFinishCheck)

        # If more jacks changed at the same time, give the next one its own
        # settle window rather than dropping it
        if self.pinsToCheck:
            print(f" * {len(self.pinsToCheck)} more jack(s) queued: {self.pinsToCheck}")
            self.bounceTimer.start(300)

    def printSnapshot(self, gpio_state):
        """Print what the chip says versus what the model believes"""
        chip = [p for p in range(12) if not (gpio_state & (1 << p))]
        model = [p for p in range(12) if self.model.getIsPinIn(p)]
        print(f"   snapshot: chip says in {chip} | model says in {model}")

    def reconcileFromSnapshot(self, gpio_state, flagged=()):
        """Jacks whose real state disagrees with the model, as interrupt events.

        A jack can change without ever raising a flag of its own: the chip
        interrupts on change from a baseline that our GPIO read resets, and the
        Pi's 50ms bouncetime can swallow the edge. The snapshot tells the truth
        either way, so trust it and let the normal path sort out the meaning.
        """
        recovered = []
        for pin in range(12):
            if pin in flagged:
                continue
            pin_value = bool(gpio_state & (1 << pin))
            physically_in = not pin_value
            if physically_in != self.pinsPhysical[pin]:
                print(f" ** pin {pin} really went {'in' if physically_in else 'out'} "
                      f"but never raised a flag - recovering it")
                self.pinsPhysical[pin] = physically_in
                recovered.append((pin, pin_value))
        return recovered

    def readInterrupts(self):
        """Which jacks raised an interrupt, with their current values.
        INTF must be read before GPIO: reading GPIO clears the interrupt,
        and the flags along with it. Returns [] when nothing is pending.
        """
        flags = self.mcp.int_flag
        if not flags:
            return [], -1
        # One read of all 16 pins -- a consistent snapshot, and clears the interrupt
        gpio_state = self.mcp.gpio
        return [(pin, bool(gpio_state & (1 << pin))) for pin in flags], gpio_state

    def delayedFinishCheck(self):
        # This just delay resetting just_checked
        print(" * delayed finished check \n")
        self.just_checked = False

        # This used to be clear_ints(), which unstuck the interrupt line but
        # threw away any flag still pending -- a plug whose edge the Pi missed
        # was then lost for good. Dispatch pending flags instead.
        pending, gpio_state = self.readInterrupts()
        if pending:
            print(f" ** delayed check found interrupt(s) the Pi never saw: {pending} - dispatching")
            self.gpioInterruptSignal.emit(pending, gpio_state)
            return

        # No flags pending, but a jack can be in a state nobody ever reported,
        # so always compare the real pins against the model here. This runs
        # about 450ms after any plug activity, which is where a jack that went
        # in alongside another one tends to get lost.
        gpio_state = self.mcp.gpio
        self.printSnapshot(gpio_state)
        recovered = self.reconcileFromSnapshot(gpio_state)
        if recovered:
            # -1: already reconciled, don't do it again on the way through
            self.gpioInterruptSignal.emit(recovered, -1)

        self.checkReoffer()

    def checkReoffer(self):
        """Offer jacks that are already seated once a caller is accepted.

        A visitor can plug the callee first, or plug a jack the logic rejects
        while no call is up. Those stay physically in but outside the model.
        The moment a caller is accepted they become meaningful, so hand them
        back to the normal path -- once, on that transition, never in a loop.
        """
        caller_plugged = self.model.phoneLine["caller"]["isPlugged"]

        if caller_plugged and not self.callerWasPlugged:
            for pin in range(12):
                if self.pinsPhysical[pin] and not self.model.getIsPinIn(pin):
                    print(f" ** pin {pin} was already seated - re-offering it "
                          f"now that a caller is on the line")
                    self.reofferedPins.add(pin)
                    if pin not in self.pinsToCheck:
                        self.pinsToCheck.append(pin)
            if self.pinsToCheck and not self.bounceTimer.isActive():
                self.bounceTimer.start(300)

        self.callerWasPlugged = caller_plugged

    def displayText(self, msg):
        self.label.setText(msg)        

    def setLED(self, flagIdx, onOrOff):
        self.pinsLed[flagIdx].value = onOrOff     

    def blinker(self):
        self.pinsLed[self.pinToBlink].value = not self.pinsLed[self.pinToBlink].value
        # print("blinking value: " + str(self.pinsLed[self.pinToBlink].value))
        
    def startBlinker(self, personIdx):
        self.pinToBlink = personIdx
        self.blinkTimer.start(600)

        # A new call is starting, and this call's caller may already be seated
        # -- someone plugged in during the welcome, or left a plug in from
        # before. Offer just that jack, once. Other seated jacks stay ignored,
        # as they always have been; offering them would only produce a
        # "wrong jack" message at the start of every call.
        if self.pinsPhysical[personIdx] and not self.model.getIsPinIn(personIdx):
            print(f" ** pin {personIdx} is already seated and is this call's "
                  f"caller - offering it")
            self.reofferedPins.add(personIdx)
            if personIdx not in self.pinsToCheck:
                self.pinsToCheck.append(personIdx)
            if not self.bounceTimer.isActive():
                self.bounceTimer.start(300)

    def stopBlinker(self):
        if self.blinkTimer.isActive():
            self.blinkTimer.stop()

    def setLEDsOff(self):
        for pinIndex in range(0, 12):
            self.setLED(pinIndex, False)

    def getAnyPinsIn(self):
        anyPinsIn = False

        for pinIndex in range(0, 12):
            if self.pins[pinIndex].value == False:
                anyPinsIn = True
        return anyPinsIn

    def stopCaptions(self):
        self.areCaptionsContinuing = False
        self.captionTimer.stop()

    def time_str_to_ms(self, time_str):
        hours, minutes, seconds_ms = time_str.split(':')
        seconds, milliseconds = seconds_ms.split(',')
        return int(hours) * 3600000 + int(minutes) * 60000 + int(seconds) * 1000 + int(milliseconds)

    # Mostly from ChatGPT
    def displayCaptions(self, fileType, file_name):
        with open('captions/' + fileType + '/' + file_name + '.srt', 'r') as f:
            self.captions = f.read().split('\n\n')
        self.areCaptionsContinuing = True
        self.captionIndex = 0

        self.display_next_caption()

    def display_next_caption(self):
        # print('got to display_next_caption')
        # nonlocal self
        if self.captionIndex < len(self.captions):
            caption = self.captions[self.captionIndex]
            # print(f'full entry: {caption}')
            if '-->' in caption:
                number, time, text = caption.split('\n', 2)
                # print(f'#: {number} time: {time}, text: {text}')
                # Stop if unplugged
                if (self.areCaptionsContinuing):
                    self.displayText(text)
                # Process time
                times = time.split(' --> ')
                # print(f'times[0]: {times[0]}')
                start_time_ms = self.time_str_to_ms(times[0])
                end_time_ms = self.time_str_to_ms(times[1])
                duration_ms = end_time_ms - start_time_ms
                if (self.areCaptionsContinuing):
                    self.captionTimer.start(duration_ms)
            self.captionIndex += 1

app = qtw.QApplication([])

win = MainWindow()
win.show()

sys.exit(app.exec_())