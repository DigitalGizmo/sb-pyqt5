# import sys
import json
from PyQt5 import QtWidgets as qtw
from PyQt5 import QtGui as qtg
from PyQt5 import QtCore as qtc
import vlc

conversationsJsonFile = open('conversations.json')
conversations = json.load(conversationsJsonFile)
personsJsonFile = open('persons.json')
persons = json.load(personsJsonFile)

class Model(qtc.QObject):
    """Main logic patterned after software proto
    """
    # The following signals are connected in/ called from control.py
    displayTextSignal = qtc.pyqtSignal(str)
    setLEDSignal = qtc.pyqtSignal(int, bool)
    # pinInEvent = qtc.pyqtSignal(int, bool)
    blinkerStart = qtc.pyqtSignal(int)
    blinkerStop = qtc.pyqtSignal()
    displayCaptionSignal = qtc.pyqtSignal(str, str)
    stopCaptionSignal = qtc.pyqtSignal()
    stopSimSignal = qtc.pyqtSignal()
    # Doesn't seem to be used
    checkPinsInEvent = qtc.pyqtSignal() 
    
    # The following signals are local
    # Need to avoid thread conflicts
    setTimeToNextSignal = qtc.pyqtSignal(int)
    # setTimeToEndSignal = qtc.pyqtSignal(int)
    setTimeToEndSignal = qtc.pyqtSignal()
    playRequestCorrectSignal = qtc.pyqtSignal()
    
    # NEW: Thread-safe signals for VLC callbacks
    endOperatorOnlySignal = qtc.pyqtSignal()
    playFullConvoSignal = qtc.pyqtSignal(int)  # currConvo
    setCallCompletedSignal = qtc.pyqtSignal()
    playFullWrongNumSignal = qtc.pyqtSignal(int)  # pluggedPersonIdx
    startPlayRequestCorrectThreadSignal = qtc.pyqtSignal()
    restartOnTimeoutSignal = qtc.pyqtSignal()
    restartOnEndTimeoutSignal = qtc.pyqtSignal()

    buzzInstace = vlc.Instance()
    buzzPlayer = buzzInstace.media_player_new()
    buzzPlayer.set_media(buzzInstace.media_new_path("/home/piswitch/Apps/sb-audio/buzzer.mp3"))
    buzzEvents = buzzPlayer.event_manager()

    toneInstace = vlc.Instance()
    tonePlayer = toneInstace.media_player_new()
    toneEvents = tonePlayer.event_manager()
    toneMedia = toneInstace.media_new_path("/home/piswitch/Apps/sb-audio/outgoing-ring.mp3")
    tonePlayer.set_media(toneMedia)

    vlcInstance = vlc.Instance()
    vlcPlayer = vlcInstance.media_player_new()
    vlcEvent = vlcPlayer.event_manager()

    def __init__(self):
        super().__init__()
        self.callInitTimer = qtc.QTimer()
        self.callInitTimer.setSingleShot(True)
        self.callInitTimer.timeout.connect(self.initiateCall)
        # signal is calling function setTimeToNext which calls callInitTimer
        # self.setTimeToNextSignal.connect(self.setTimeToNext)
        self.setTimeToNextSignal.connect(self.callInitTimer.start)

        self.reconnectTimer = qtc.QTimer()
        self.reconnectTimer.setSingleShot(True)
        self.reconnectTimer.timeout.connect(self.reCall)

        self.resetEndTimer = qtc.QTimer()
        self.resetEndTimer.setSingleShot(True)
        # self.resetEndTimer.timeout.connect(self.stopSimSignal.emit())
        self.resetEndTimer.timeout.connect(self.resetAtEnd)

        self.playRequestCorrectSignal.connect(self.playRequestCorrect)
        self.setTimeToEndSignal.connect(self.startEndTimer)

        self.restartOnTimeoutTimer = qtc.QTimer()
        self.restartOnTimeoutTimer.setSingleShot(True)
        self.restartOnTimeoutTimer.timeout.connect(self.handleRestartOnTimeout)


        # signal calls timer directly
        # self.checkDualUnplugSignal.connect(self.dualUnplugTimer.start)
        # self.dualUnplugTimer.timeout.connect(self.checkDualUnplug)
        
        # NEW: Connect thread-safe VLC signals to their handlers
        self.endOperatorOnlySignal.connect(self.handleEndOperatorOnly)
        self.playFullConvoSignal.connect(self.handlePlayFullConvo)
        self.setCallCompletedSignal.connect(self.handleSetCallCompleted)
        self.playFullWrongNumSignal.connect(self.handlePlayFullWrongNum)
        self.startPlayRequestCorrectThreadSignal.connect(self.handleStartPlayRequestCorrect)
        self.restartOnEndTimeoutSignal.connect(self.handleRestartOnEndTimeout)
        self.restartOnTimeoutSignal.connect(self.startRestartOnTimeoutTimer)

        self.reset()

    def reset(self):
        self.stopAllAudio()
        self.stopTimers()

        # Put pinsIn here in model where it's used more often
        # rather than in control which would require a lot of signaling.
        self.pinsIn = [False,False,False,False,False,False,False,False,False,False,False,False,False,False]
        
        self.currConvo = 0
        self.currCallerIndex = 0
        self.currCalleeIndex = 0
        # self.whichLineInUse = -1
        self.currStopTime = 0
        self.currPersonIdx = 0

        self.incrementJustCalled = False
        # self.reCallLine = 0 # Workaround timer not having params
        self.silencedCallLine = 0 # Workaround timer not having params
        # self.requestCorrectLine = 0 # Workaround timer not having params

        self.NO_UNPLUG_STATUS = 0
        self.WRONG_NUM_IN_PROGRESS = 1
        self.OP_ONLY_IN_PROGRESS = 2
        self.REPLUG_IN_PROGRESS = 3
        self.CALLER_UNPLUGGED = 5

        self.phoneLine = {
                "isEngaged": False,
                "unPlugStatus": self.NO_UNPLUG_STATUS,
                "caller": {"index": 99, "isPlugged": False},
                "callee": {"index": 99, "isPlugged": False}
                # "audioTrack": vlc.MediaPlayer("/home/piswitch/Apps/sb-audio/1-Charlie_Operator.mp3")
            }

        # self.displayTextSignal.emit("Keep your ears open for incoming calls!")

    def stopTimers(self):
        if self.callInitTimer.isActive():
            self.callInitTimer.stop()
        if self.reconnectTimer.isActive():
            self.reconnectTimer.stop()
        # if self.silencedCalTimer.isActive():
        #     self.silencedCalTimer.stop()


    def stopAllAudio(self):
        # if self.callInitTimer.isActive():
        #     self.callInitTimer.stop()

        self.buzzPlayer.stop()
        self.tonePlayer.stop()
        # self.vlcPlayers[0].stop()
        self.vlcPlayer.stop()

    def startRestartOnTimeoutTimer(self):
        """Start the restart timeout timer in the main thread"""
        self.restartOnTimeoutTimer.start(2000)

    def setPinIn(self, pinIdx, value):
        self.pinsIn[pinIdx] = value

    # Remove
    # def getPinInLine(self, pinIdx):
    #     return self.pinsInLine[pinIdx]
    
    # Used by wiggle detect in control
    def getIsPinIn(self, pinIdx):
        return self.pinsIn[pinIdx]

    def initiateCall(self):
        self.incrementJustCalled = False

        if (self.currConvo < 9):
            print(f'Setting currCallerIndex to {conversations[self.currConvo]["caller"]["index"]}'
                  f' currConvo: {self.currConvo}')
            self.currCallerIndex =  conversations[self.currConvo]["caller"]["index"]
            # Set "target", person being called
            self.currCalleeIndex = conversations[self.currConvo]["callee"]["index"]
            # This just rings the buzzer. Next action will
            # be when user plugs in a plug 
            # buzzTrack.volume = .6   

            self.buzzEvents.event_attach(vlc.EventType.MediaPlayerEndReached, 
                self.restartOnTimeout) 

            self.buzzPlayer.play()
            self.blinkerStart.emit(conversations[self.currConvo]["caller"]["index"])
            self.displayTextSignal.emit("Incoming call..")
            
            print(f'- New convo {self.currConvo} being initiated by: ' 
                    f'{persons[conversations[self.currConvo]["caller"]["index"]]["name"]}')
        else:
            # Play congratulations
            print("Congratulations - done!")
            self.playFinished()

    def playHello(self, _currConvo): # , lineIndex
        # print(" -- got to playHello")
        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/" + 
            conversations[_currConvo]["helloFile"] + ".mp3")
        self.vlcPlayer.set_media(media)
        # For convo idxs 3 and 7 there is no full convo, so end after hello.
        # Attach event before playing
        if (_currConvo == 3 or  _currConvo == 8):
            print(f" -- got to currConv = 3 or 8 -- Operator only ")
            # Set call status to operator only
            self.phoneLine["unPlugStatus"] = self.OP_ONLY_IN_PROGRESS
            self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
                self.endOperatorOnlyHello) #  _currConvo, 
        else: # This is a regular call and we need to be prepared for the sim being 
            # abandoned, i.e. no one ever plugs in the callee
            print(' - Regular call, attaching event in case we time out')
            self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.restartOnTimeout) 


        # Proceed with playing -- event may or may not be attached            
        self.vlcPlayer.play()
        # Send msg to screen
        self.displayCaptionSignal.emit('hello', conversations[_currConvo]["helloFile"])


    def endOperatorOnlyHello(self, event): # , lineIndex
        """VLC callback - must be thread-safe"""
        print("  - VLC callback endOperatorOnlyHello - emitting signal")
        
        if event != None:
            try:
                self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
            except:
                pass
        
        # Emit signal to main thread
        self.endOperatorOnlySignal.emit()

    def handleEndOperatorOnly(self):
        """Handle operator-only ending in main thread"""
        print("  - handleEndOperatorOnly in main thread")
        
        self.clearTheLine()

        # Check if we've already incremented
        if not self.incrementJustCalled:
            print(f" - Hello-only ended.  Bump currConvo from {self.currConvo}")
            self.incrementJustCalled = True
            self.currConvo += 1
            # Now safe to use timer in main thread
            self.setTimeToNextSignal.emit(1000)
        else:
            print(f" - Hello-only ended, but currConvo already incremented to {self.currConvo}")

    def playConvo(self, currConvo): # , lineIndex
        """
        This just plays the outgoing tone and then starts the full convo
        """
        print(f" -- got to play convo, currConvo: {currConvo}")
        # Store currConvo for later use
        self._pendingConvo = currConvo
        # Long VLC way of creating callback
        self.toneEvents.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.playFullConvo) # Now without currConvo parameter
        self.tonePlayer.set_media(self.toneMedia)
        self.tonePlayer.play()

    def playFullConvo(self, event):
        """VLC callback - must be thread-safe"""
        if event != None:
            try:
                self.toneEvents.event_detach(vlc.EventType.MediaPlayerEndReached)
            except:
                pass
        
        # Emit signal with stored currConvo
        self.playFullConvoSignal.emit(self._pendingConvo)

    def handlePlayFullConvo(self, _currConvo):
        """Handle playing full conversation in main thread"""
        print(f" -- PlayFullConvo {_currConvo}")
        # Set callback for convo track finish
        self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.setCallCompleted)
        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/" + 
            conversations[_currConvo]["convoFile"] + ".mp3")
        self.vlcPlayer.set_media(media)
        self.vlcPlayer.play()
        self.displayCaptionSignal.emit('convo', conversations[_currConvo]["convoFile"])

    def playWrongNum(self, pluggedPersonIdx): # , lineIndex
        print(f" -- [2] got to play wrong number, currConvo: {self.currConvo}")
        # Store pluggedPersonIdx for later use
        self._pendingPluggedPerson = pluggedPersonIdx
        # Long VLC way of creating callback
        self.toneEvents.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.playFullWrongNum)
        self.tonePlayer.set_media(self.toneMedia)
        self.tonePlayer.play()

    def playFullWrongNum(self, event): # , lineIndex
        """VLC callback - must be thread-safe"""
        print("  - About to detach toneEvent in playFullWrongNum")
        
        if event != None:
            try:
                self.toneEvents.event_detach(vlc.EventType.MediaPlayerEndReached)
            except:
                pass
        
        # Emit signal with stored pluggedPersonIdx
        self.playFullWrongNumSignal.emit(self._pendingPluggedPerson)

    def handlePlayFullWrongNum(self, pluggedPersonIdx):
        """Handle playing wrong number in main thread"""
        self.displayTextSignal.emit(persons[pluggedPersonIdx]["wrongNumText"])

        print(f"  -- Play Wrong Num person {pluggedPersonIdx}")
        # Set callback for wrongNum track finish
        self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.startPlayRequestCorrect)
        
        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/" + 
            persons[pluggedPersonIdx]["wrongNumFile"] + ".mp3")
        self.vlcPlayer.set_media(media)
        self.vlcPlayer.play()

    def startPlayRequestCorrect(self, event): # , lineIndex
        """VLC callback - must be thread-safe"""
        print("  - About to detach vlcEvent in startPlayRequestCorrect")

        if event is not None:
            try:
                self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
            except:
                pass

        # Emit signal to main thread
        self.startPlayRequestCorrectThreadSignal.emit()

    def handleStartPlayRequestCorrect(self):
        """Handle request correct in main thread"""
        self.playRequestCorrectSignal.emit()

    # Reply from caller saying who caller really wants
    def playRequestCorrect(self):
        print(f"  - got to playRequestCorrect, currConvo: {self.currConvo}")
        # Transcript for correction
        self.displayTextSignal.emit(conversations[self.currConvo]["retryAfterWrongText"])

        print("  - About to detach vlcEvent in PlayRequestCorrect")
        self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached) 

        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/" + 
            conversations[self.currConvo]["retryAfterWrongFile"] + ".mp3")
        
        self.vlcPlayer.set_media(media)
        self.vlcPlayer.play()
        # At this point we hope user unplugs wrong number
        # Will be handled by "unPlug"

    def playFinished(self):
        self.toneEvents.event_detach(vlc.EventType.MediaPlayerEndReached)         

        self.displayTextSignal.emit("Congratulations -- you finished your first shift as a switchboard operator!")
        # print(f"-- PlayFullConvo {_currConvo}, lineIndex: {lineIndex}")

        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/" + 
            "FinishedActivity.mp3")
        self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)

        self.vlcPlayer.set_media(media)

        self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.restartOnEndTimeout) 

        self.vlcPlayer.play()

    # def setTimeToNext(self, timeToWait):
    #     self.callInitTimer.start(timeToWait)   
             

    def setTimeReCall(self, _currConvo): 
        print("got to setTimeReCall")
        # currConvo is already global
        self.reconnectTimer.start(1000)
        # reconnectTimer will call reCall

    def reCall(self):
        print("got to reCall")
        # Hack: receives reCallLine globally 
        self.playHello(self.currConvo) #, self.reCallLine
        # calling playHello directly with callback would send event param

    def handlePlugIn(self, personIdx):
        """triggered by control.py
        """
        print(f' - Start handlePlugIn, personIdx: {personIdx}'
              f' is caller plugged: {self.phoneLine["caller"]["isPlugged"]}')
        # ********
        # Fresh plug-in -- aka caller wasn't plugged yet
        # Is this new use of this line -- caller has not been plugged in.
        # *******/
        if (not self.phoneLine["caller"]["isPlugged"]): # New line - Caller not plugged
            # Did user plug into the actual caller?
            if personIdx == self.currCallerIndex: # Correct caller
                # Turn this LED on
                self.setLEDSignal.emit(personIdx, True)
                # Set this person's jack to plugged
                self.setPinIn(personIdx, True)

                # Set this line as having caller plugged
                self.phoneLine["caller"]["isPlugged"] = True
                # Set identity of caller on this line
                self.phoneLine["caller"]["index"] = personIdx;				
                # print(f' - Just set caller {self.phoneLine["caller"]["index"]} to True')

                # Set this line in use only we have gotten this success
                # self.whichLineInUse = lineIdx

                # See software app for extended debug message here
                # Stop Buzzer. 
                self.buzzPlayer.stop()
                # Blinker handled in control.py
                self.blinkerStop.emit()

                # print(f" ++ New plugin- prev line in use: {self.prevLineInUse}")

                #  Handle case where caller was unplugged
                if (self.phoneLine["unPlugStatus"] == self.CALLER_UNPLUGGED):
                    print(f"  - Caller was unplugged")
                    """ more logic here  
                    """
                    if (self.phoneLine["callee"]["isPlugged"] == True):
                        # if (correct callee??)
                        # Stop Hello/Request
                        self.vlcPlayer.stop()
                        # set line engaged
                        self.phoneLine["unPlugStatus"] = self.NO_UNPLUG_STATUS
                        self.phoneLine["isEngaged"] = True
                        self.phoneLine["caller"]["isPlugged"] = True
                        # Start conversation without the ring
                        # For now anyway can't play full convo without sending event so

                        # self.playFullConvoNoEvent(self.currConvo)
                        print("  - playFullConvo w/o event ")
                        # Direct call since we're in main thread
                        self.handlePlayFullConvo(self.currConvo)

                    else:
                        print('   We should not get here');

                else: # Regular, just play incoming Hello/Request
                    self.playHello(self.currConvo) 
                
                # Set prev for use in next call. Here??
                # print(f"setting prev line in use from {p}")
                # self.prevLineInUse = self.whichLineInUse
            else:
                print("wrong jack -- or wrong line")
                self.displayTextSignal.emit("That's not the jack for the person who is asking you to connect!")

        #********
        # Other end of the line -- caller is plugged, so this must be the callee
        #********/
        else: # caller is plugged
			# Ignore the following if this is an operator-only call in progress
            print(' -- else caller plugged. unPlugStatus: ' + str(self.phoneLine["unPlugStatus"]))
            if (not self.phoneLine["unPlugStatus"] == self.OP_ONLY_IN_PROGRESS):

                # Whether or not this is correct callee -- turn LED on.
                self.setLEDSignal.emit(personIdx, True)
                # Set pinsIn True
                self.setPinIn(personIdx, True)
                # Stop the hello operator track,  whether this is the correct
                # callee or not
                self.vlcPlayer.stop()
                # Also stop captions
                self.stopCaptionSignal.emit()

                # Set callee -- used by unPlug even if it's the wrong number
                self.phoneLine["callee"]["index"] = personIdx
                if (personIdx == self.currCalleeIndex): # Correct callee
                    print(f" - Plugged into correct callee, idx: {personIdx}")
                    # Set this line as engaged
                    self.phoneLine["isEngaged"] = True
                    # Also set line callee plugged
                    self.phoneLine["callee"]["isPlugged"] = True

                    # # Silence incoming Hello/Request, if necessary
                    # self.vlcPlayers[lineIdx].stop()
                    self.playConvo(self.currConvo)

                else: # Wrong number
                    print(" -- |1| just plugged into wrong number")
                    self.phoneLine["unPlugStatus"] = self.WRONG_NUM_IN_PROGRESS

                    self.playWrongNum(personIdx) 

            else:
                print("got to Tressa erroneous plug-in")

    def shouldRetryCall(self, stopTime):
            """Determine if call should be retried based on stop time"""
            return stopTime < conversations[self.currConvo]["okTimeConvo"]

    def handleUnPlug(self, personIdx): 
        """ triggered by control.py
        """
        print( f" - Index {personIdx} Unplugged with unplugStatus of: {self.phoneLine['unPlugStatus']}\n"
               f"     while line isEngaged = {self.phoneLine['isEngaged']}"
            )
        # if not during restart!

        # ---- Conversation in progress --- 
        if (self.phoneLine["isEngaged"]):
            # If conversation is in progress -- engaged (implies correct callee)
            print(f'  - Unplugging a call in progress person id: {persons[personIdx]["name"]} ' )
            # Get stop time
            stopTime = self.vlcPlayer.get_time()
            # print(f'  -- stop time: {stopTime}')

            # Stop the audio
            self.vlcPlayer.stop()
            # Stop subtitles
            self.stopCaptionSignal.emit()
            # Clear Transcript 
            self.displayTextSignal.emit("Call disconnected..")

            self.currPersonIdx = personIdx
            self.currStopTime = stopTime
            print(' - got to engaged unplug')

            # Engaged call, callee unplugged
            if (self.phoneLine["callee"]["index"] == personIdx):  
                print('   Unplugging callee. stopTime: ' + str(stopTime))
                # Turn off callee LED
                self.setLEDSignal.emit(self.phoneLine["callee"]["index"], False)
                # Mark callee unplugged
                self.phoneLine["callee"]["isPlugged"] = False
                self.phoneLine["isEngaged"] = False

                # If Early in call, retry
                if self.shouldRetryCall(stopTime):
                    # Restart this answer to call
                    # stop captions
                    self.stopCaptionSignal.emit()
                    # Leave caller plugged in, replay hello
                    self.setTimeReCall(self.currConvo)
                else:
                    # Late in call -- end convo and move on
                    print(f'  - stopped with time: {stopTime}')
                    # Direct call since we're in main thread
                    self.handleSetCallCompleted()

            # Engaged call, caller unplugged
            elif (self.phoneLine["caller"]["index"] == personIdx): 
                print(" Caller just unplugged")
                self.phoneLine["caller"]["isPlugged"] = False
                self.phoneLine["isEngaged"] = False
                # Also
                self.phoneLine["unPlugStatus"] = self.CALLER_UNPLUGGED
                # Turn off caller LED
                self.setLEDSignal.emit(self.phoneLine["caller"]["index"], False)
                
                # For caller unplug, we don't check time - always move to next
                # signal calls callInitTimer which calls initiateCall	
                self.setTimeToNextSignal.emit(1000)					
            else: 
                print('    This should not happen')

        # ---- Conversation NOT in progress --- 
        else:   
            # Phone line is not engaged -- isEngaged == False
            print(f' - unplug while not engaged, callee index: {self.phoneLine["callee"]["index"]}'
                  f'    caller index: {self.phoneLine["caller"]["index"]}')

            print(f'  -- unplugStatus: {self.phoneLine["unPlugStatus"]}')

            if (self.phoneLine["caller"]["isPlugged"] == True):
                # Caller has initiated a call

                # If this is the caller being unplugged (erroneously or early)
                # Correct caller unplugging?
                if (personIdx == self.phoneLine["caller"]["index"]):
                    print("     caller unplugged")
                    stopTime = self.vlcPlayer.get_time()
                    self.vlcPlayer.stop() 
                    #  LED handled by either condition below
                    # If this is a hello only call # And if we're close enough to the end
                    if ((self.currConvo == 3 or  self.currConvo == 8) and
                        stopTime > conversations[self.currConvo]["okTimeHello"]):
                        # Close enough to end, move on 
                        print(f'  - stopped operator only caller with time: {stopTime}')
                        # Direct call since we're in main thread
                        self.handleEndOperatorOnly()
                    else:
                        self.clearTheLine()
                        self.callInitTimer.start(1000)
                elif (self.phoneLine["unPlugStatus"] == self.WRONG_NUM_IN_PROGRESS):
                    # Unplugging wrong num
                    print(f' -- |2| Unplug on wrong number, personIdx: {personIdx}')

                    # Don't stop the request for the right number so soon
                    # self.vlcPlayer.stop() 

                    # Cover for before personidx defined
                    if (personIdx < 99):
                        self.setLEDSignal.emit(personIdx, False)
                    # clear the unplug status
                    self.phoneLine["unPlugStatus"] = self.NO_UNPLUG_STATUS
                else: # Not unplugging wrong - do nothing
                    print(" just unplugging to free up a plug")
            elif (self.phoneLine["unPlugStatus"] == self.CALLER_UNPLUGGED):
                print(" - callee unplugged while caller unplugged ")
                # Turn off LED
                self.setLEDSignal.emit(personIdx, False)
                # Set unplug status to default
                self.phoneLine["unPlugStatus"] = self.NO_UNPLUG_STATUS
                # This should lead to just starting the current call over
                   
            else: # caller not plugged
                print(" - nothing going on, just unplugging ")

        # After all is said and done, this was unplugged, So, set pinIn False
        # self.setPinInLine(personIdx, -1)
        self.setPinIn(personIdx, False)
        print(f" - pin {personIdx} is now {self.pinsIn[personIdx]}")

    def handleDualUnplug(self, pin1, pin2):
        """Handle the case where both caller and callee unplug simultaneously during active call"""
        print(f" - handleDualUnplug: pins {pin1} and {pin2} unplugged together during active call")
        
        # Get stop time before stopping audio
        stopTime = self.vlcPlayer.get_time()
        print(f"  - Dual unplug at time: {stopTime}")
        
        # Stop the audio immediately
        self.vlcPlayer.stop()
        
        # Stop subtitles
        self.stopCaptionSignal.emit()
        
        # Clear both pins
        self.setPinIn(pin1, False)
        self.setPinIn(pin2, False)
        
        # Turn off both LEDs
        self.setLEDSignal.emit(pin1, False)
        self.setLEDSignal.emit(pin2, False)
        
        # Check if we should retry based on time
        if self.shouldRetryCall(stopTime):
            print(f"  - Early dual unplug (time: {stopTime}), restarting call")
            # Clear the line but keep the call state
            self.phoneLine["caller"]["isPlugged"] = False
            self.phoneLine["callee"]["isPlugged"] = False
            self.phoneLine["isEngaged"] = False
            # Display message
            self.displayTextSignal.emit("Both parties disconnected early - restarting call")
            # Restart the current call
            self.setTimeToNextSignal.emit(1000)  # This will re-initiate current call
        else:
            print(f"  - Late dual unplug (time: {stopTime}), moving to next call")
            # Clear the line completely
            self.clearTheLine()
            # Display appropriate message
            self.displayTextSignal.emit("Call completed - both parties disconnected")
            # Move to next call
            self.currConvo += 1
            self.setTimeToNextSignal.emit(2000)  # 2 second delay before next call

    # def setDualUnplugTimer(self):
    #     # Timer will call 
    #     self.dualUnplugTimer.start(90)

    def setCallCompleted(self, event=None): #, _currConvo, lineIndex
        """VLC callback - must be thread-safe"""
        # Disable callback if present
        if event != None:
            try:
                self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
            except:
                pass
        
        # Emit signal to main thread
        self.setCallCompletedSignal.emit()

    def handleSetCallCompleted(self):
        """Handle call completion in main thread"""
        print(f" -- setCallCompleted. Convo: {self.currConvo}")
        # Stop call
        self.stopCall()

        # Workaround to stop double calling
        if not self.incrementJustCalled:
            self.incrementJustCalled = True
            print(f' -  increment from {self.currConvo} and start regular timer for next call.')
            # Uptick currConvo here, when call is complete
            self.currConvo += 1
            # Use signal rather than calling callInitTimer bcz threads
            self.setTimeToNextSignal.emit(1000)

    def stopCall(self): # , lineIndex
        self.clearTheLine()
        # Reset volume -- in this line was silenced by interrupting call
        # self.vlcPlayers[self.prevLineInUse].audio_set_volume(100)

    def clearTheLine(self):
        # Clear the line settings
        self.phoneLine["caller"]["isPlugged"] = False
        self.phoneLine["callee"]["isPlugged"] = False
        self.phoneLine["isEngaged"] = False
        self.phoneLine["unPlugStatus"] = self.NO_UNPLUG_STATUS
        # self.prevLineInUse = -1
        # Turn off the LEDs
        self.setLEDSignal.emit(self.phoneLine["caller"]["index"], False)
        # Can't turn off callee led if callee index hasn't been defined
        # print(f'About to try to turn off .callee.index: {self.phoneLines[lineIdx]["callee"]["index"]}')
        if (self.phoneLine["callee"]["index"] < 90):
            # console.log('got into callee index not null');
            self.setLEDSignal.emit(self.phoneLine["callee"]["index"], False)

    def handleStart(self):
        """Just for startup
        """
        print(" - got to model.handleStart")
        # Set callback for welcome track finish
        self.vlcEvent.event_attach(vlc.EventType.MediaPlayerEndReached, 
            self.afterWelcome)  
        media = self.vlcInstance.media_new_path("/home/piswitch/Apps/sb-audio/Welcome.mp3")
        self.vlcPlayer.set_media(media)
        self.vlcPlayer.play()
        # self.displayCaptionSignal.emit('convo', conversations[_currConvo]["convoFile"])
        self.displayTextSignal.emit("Welcome to the switchboard game. \nIt's your turn to be a switchboard operator! \nHere comes the first call.")

    def afterWelcome(self, event):
        self.setTimeToNextSignal.emit(1000) # calls setTimeToNext

    def restartOnTimeout(self, event):
        """VLC callback - must be thread-safe"""
        print(' - auto starting reset (VLC callback) -- calling restartOnTimeoutTimer')
        try:
            self.buzzEvents.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass
        
        # Need delay befor sending restartOnTimeoutSignal
        try:
            self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass
        
        # Emit signal to main thread to start the timer
        self.restartOnTimeoutSignal.emit()

    def handleRestartOnTimeout(self):
        """Handle restart in main thread"""
        print(' - handling restart in main thread')
        self.blinkerStop.emit()
        self.stopSimSignal.emit()

    def restartOnEndTimeout(self, event):
        """VLC callback - must be thread-safe"""
        print(' - Starting reset after End (VLC callback)')
        try:
            self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass

        # Emit signal to main thread
        self.restartOnEndTimeoutSignal.emit()

    def handleRestartOnEndTimeout(self):
        """Handle restart after end in main thread"""
        # This signal will call startEndTimer
        self.setTimeToEndSignal.emit()

    def startEndTimer(self):
        # Timer will call self.stopSimSignal.emit()
        self.resetEndTimer.start(2000)

    def resetAtEnd(self):
        # Maybe this could go directly in callback?
        self.stopSimSignal.emit()

    def detachAllEventHandlers(self):
        # Detach all VLC event handlers
        try:
            self.buzzEvents.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass
        
        try:
            self.toneEvents.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass
        
        try:
            self.vlcEvent.event_detach(vlc.EventType.MediaPlayerEndReached)
        except:
            pass