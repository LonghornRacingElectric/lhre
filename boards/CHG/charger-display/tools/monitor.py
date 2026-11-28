#!/usr/bin/env python3
"""Read USB debug UART without resetting the ESP32 (macOS/POSIX)."""
import argparse, os, select, termios, time
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--port',default='/dev/cu.usbserial-110')
p.add_argument('--seconds',type=float,default=8)
a=p.parse_args()
fd=os.open(a.port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
old=termios.tcgetattr(fd)
try:
    cfg=termios.tcgetattr(fd)
    cfg[0]=0;cfg[1]=0;cfg[2]=termios.CS8|termios.CREAD|termios.CLOCAL;cfg[3]=0
    cfg[4]=cfg[5]=termios.B115200
    cfg[6][termios.VMIN]=0;cfg[6][termios.VTIME]=0
    termios.tcsetattr(fd,termios.TCSANOW,cfg)
    end=time.monotonic()+a.seconds
    while time.monotonic()<end:
        ready,_,_=select.select([fd],[],[],min(.5,max(0,end-time.monotonic())))
        if ready:
            data=os.read(fd,4096)
            if data: print(data.decode('utf-8',errors='replace'),end='',flush=True)
finally:
    termios.tcsetattr(fd,termios.TCSANOW,old);os.close(fd)
