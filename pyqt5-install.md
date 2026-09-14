# PyQt5 installation
Installing PyQt5 is tricky, have been unable to install via pip.

So: 
- **Install system-wide PyQt5:**
    ```bash
    sudo apt update
    sudo apt install python3-pyqt5 python3-pyqt5-dev python3-pyqt5.qttools
    ```
    
- **Find where the system PyQt5 is installed:**
    ```bash
    python3 -c "import PyQt5; print(PyQt5.__file__)"
    ```
    Usually it's in `/usr/lib/python3/dist-packages/`
- **Create symlinks in your virtual environment:**
    ```bash
    # Make sure you're in your activated venv
    source venv/bin/activate
    
    # Find your venv's site-packages directory
    python -c "import site; print(site.getsitepackages())"
    
    # Create symlinks (adjust paths as needed)
    ln -s /usr/lib/python3/dist-packages/PyQt5 venv/lib/python3.*/site-packages/
    # ln -s /usr/lib/python3/dist-packages/sip.* venv/lib/python3.*/site-packages/
    ```

- in our case:
```
/home/piswitch/Apps/sb-pyqt5/venv/lib/python3.9/site-packages
```
- note: sb-pyqt4 and earlier used `.venv`; from sb-pyqt5 on the venv is `venv`, unhidden.