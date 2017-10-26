#!/usr/bin/env python

import sys
from std_msgs.msg import String
from Tkinter import *
from time import localtime, strftime, sleep
import math
import numpy as np
import threading
import Queue
import inputs
import serial
import csv
from copy import deepcopy


class App:
    def __init__(self, master):
        self.master = master
        self.poll()  # Start polling

    def poll(self):
        redraw()
        self.master.after(10, self.poll)


class GamepadReader(threading.Thread):
    def __init__(self, master):
        self.master = master
        threading.Thread.__init__(self)
        self.terminate = False
        self.gamepadOK = False
        self.gamepadUnplugged = False
        self.gamepadIOError = False

    def stop(self):
        self.terminate = True
        self._Thread__stop()

    def run(self):
        while not self.terminate:
            if not self.gamepadOK:
                self.devices = inputs.DeviceManager()
                try:
                    gamepad = self.devices.gamepads[0]
                    logMessage("Gamepad connected")
                    self.gamepadOK = True
                    self.gamepadUnplugged = False
                except IndexError:
                    self.gamepadOK = False
                    if self.gamepadUnplugged == False:
                        logMessage("Gamepad not found")
                        self.gamepadUnplugged = True
                    sleep(1)
                    continue
            try:
                # Get joystick input
                events = gamepad.read()
                for event in events:
                    self.processEvent(event)
                self.gamepadIOError = False
            except IOError:
                self.gamepadOK = False
                if self.gamepadIOError == False:
                    logMessage("Gamepad I/O error")
                    self.gamepadIOError = True
                sleep(1)
                continue

    def processEvent(self, event):
        #print(event.ev_type, event.code, event.state)
        if event.code == 'ABS_X':
            global inputLJSX
            inputLJSX = event.state
        elif event.code == 'ABS_Y':
            global inputLJSY
            inputLJSY = event.state
        elif event.code == 'ABS_RX':
            global inputRJSX
            inputRJSX = event.state
        elif event.code == 'ABS_RY':
            global inputRJSY
            inputRJSY = event.state


class GamepadHandler(threading.Thread):
    def __init__(self, master):
        self.master = master
        # Threading vars
        threading.Thread.__init__(self)
        self.terminate = False
        self.paused = True
        self.triggerPolling = True
        self.cond = threading.Condition()
        # Input vars
        self.target = deepcopy(targetHome[selectedLeg])
        self.speed = [0, 0, 0]
        self.inputLJSXNormed = 0
        self.inputLJSYNormed = 0
        self.inputRJSXNormed = 0
        self.inputRJSYNormed = 0
        self.dt = 0.005  # 5 ms

    def stop(self):
        self.terminate = True
        self._Thread__stop()

    def run(self):
        while not self.terminate:
            with self.cond:
                if self.paused:
                    self.cond.wait()  # Block until notified
                    self.triggerPolling = True
                elif self.triggerPolling:
                    self.pollInputs()
                    self.pollIK()
                    self.triggerPolling = False

    def pause(self):
        with self.cond:
            self.paused = True

    def resume(self):
        with self.cond:
            self.paused = False
            self.cond.notify()  # Unblock self if waiting

    def pollInputs(self):
        # World X
        global inputLJSY
        self.inputLJSYNormed = self.filterInput(-inputLJSY)
        self.target[0, 3], self.speed[0] = self.updateMotion(self.inputLJSYNormed, self.target[0, 3], self.speed[0])
        # World Y
        global inputLJSX
        self.inputLJSXNormed = self.filterInput(-inputLJSX)
        self.target[1, 3], self.speed[1] = self.updateMotion(self.inputLJSXNormed, self.target[1, 3], self.speed[1])
        # World Z
        global inputRJSY
        self.inputRJSYNormed = self.filterInput(-inputRJSY)
        self.target[2, 3], self.speed[2] = self.updateMotion(self.inputRJSYNormed, self.target[2, 3], self.speed[2])
        with self.cond:
            if not self.paused:
                self.master.after(int(self.dt*1000), self.pollInputs)

    def pollIK(self):
        global target, speed
        target = deepcopy(self.target)
        speed = deepcopy(self.speed)
        runLegIK(legs[selectedLeg], target)
        with self.cond:
            if not self.paused:
                self.master.after(int(self.dt*1000), self.pollIK)

    def filterInput(self, i):
        if (i > 3277) or (i < -3277):  # ~10%
            if i > 3277:
                oldMax = 32767
            elif i < -3277:
                oldMax = 32768
            inputNormed = math.copysign(1.0, abs(i)) * rescale(i, 0, oldMax, 0, 1.0)
        else:
            inputNormed = 0
        return inputNormed

    def updateMotion(self, i, target, speed):
        mu = 1.0
        m = 1.0
        u0 = speed
        F = inputForceMax*i - dragForceCoef*u0  # Force minus linear drag
        a = F/m
        t = self.dt
        x0 = target
        # Equations of motion
        u = u0 + a*t
        x = x0 + u0*t + 0.5*a*math.pow(t, 2)
        # Update self
        target = x
        speed = u
        return target, speed


class SerialHandler(threading.Thread):
    def __init__(self, master):
        self.master = master
        threading.Thread.__init__(self)
        self.terminate = False
        self.cond = threading.Condition()
        # Input vars
        self.ser = 0
        self.port = "/dev/ttyUSB0"
        self.serialOK = False
        self.serialDisconnected = False
        self.dt = 0.05  # 50 ms
        self.pollSerial()

    def stop(self):
        self.terminate = True
        self._Thread__stop()

    def run(self):
        while not self.terminate:
            if not self.serialOK:
                try:
                    self.ser = serial.Serial(self.port, 38400)
                    logMessage("Serial port " + self.port + " connected")
                    self.serialOK = True
                    self.serialDisconnected = False
                except serial.SerialException:
                    self.serialOK = False
                    if self.serialDisconnected == False:
                        logMessage("Serial port " + self.port + " not connected")
                        self.serialDisconnected = True
            else:
                sleep(2)

    def pollSerial(self):
        if self.serialOK:
            writeStr = ""
            for i in range(len(legs[selectedLeg].angles)):
                if (selectedLeg % 2 == 0):
                    # Left side
                    # Joint 2 needs its direction inverted
                    if i == 1:
                        x = int( rescale(-legs[selectedLeg].angles[i], -180.0, 180.0, 0, 1023) )
                    else:
                        x = int( rescale(legs[selectedLeg].angles[i], -180.0, 180.0, 0, 1023) )
                else:
                    # Right side
                    # All joints except for 1 and 5 are mirrors of left side
                    if (i == 2) or (i == 3):
                        x = int( rescale(-legs[selectedLeg].angles[i], -180.0, 180.0, 0, 1023) )
                    else:
                        x = int( rescale(legs[selectedLeg].angles[i], -180.0, 180.0, 0, 1023) )
                writeStr += str(i+1) + "," + str(x)
                if i < (len(legs[selectedLeg].angles) - 1):
                    writeStr += ","
                else:
                    writeStr += "\n"
            #print "writeStr: ", writeStr
            try:
                self.ser.write(writeStr)
            except serial.SerialException:
                logMessage("Serial write error")
                self.ser.close()
                self.serialOK = False
        self.master.after(int(self.dt*1000), self.pollSerial)

    def closeSerial(self):
        if self.serialOK:
            self.ser.close()


class Spine():
    def __init__(self, id, joints, angles, tfSpineBaseInWorld):
        self.id = id
        self.joints = joints
        self.angles = angles
        self.tfSpineBaseInWorld = tfSpineBaseInWorld


class Leg():
    def __init__(self, id, joints, angles, tfLegBaseInSpineBase):
        self.id = id
        self.joints = joints
        self.angles = angles
        self.tfLegBaseInSpineBase = tfLegBaseInSpineBase


class Joint():
    def __init__(self, id, tfJointInPrevJoint, tfJointInWorld):
        self.id = id
        self.tfJointInPrevJoint = tfJointInPrevJoint
        self.tfJointInWorld = tfJointInWorld


def initSpine():
    tmpTF = np.matrix( [ [  1,  0,  0,  0],
                         [  0,  1,  0,  0],
                         [  0,  0,  1,  0],
                         [  0,  0,  0,  1] ] )

    global spine
    spineAngles = [0, 0, 0]
    spine = Spine( "B", initSpineJoints(21), spineAngles, tmpTF )


def initSpineJoints(startingJoint):
    tmpTF = np.matrix( [ [  1,  0,  0,  0],
                         [  0,  1,  0,  0],
                         [  0,  0,  1,  0],
                         [  0,  0,  0,  1] ] )
    joints = [0, 0, 0]
    joints[0] = Joint(startingJoint, tmpTF, tmpTF)
    joints[1] = Joint("Dummy", tmpTF, tmpTF)
    joints[2] = Joint(startingJoint + 1, tmpTF, tmpTF)
    return joints


def initLegs():
    lengthD = 100
    widthD = 50
    heightD = 10

    # TODO: Position leg bases more accurately

    # +135 around Y
    tfFLBaseInSpineBase = np.matrix( [ [ -0.707,  0,   0.707,  0],
                                       [      0,  1,       0,  0],
                                       [ -0.707,  0,  -0.707,  0],
                                       [      0,  0,       0,  1] ] )
    tfFLBaseInSpineBase *= np.matrix( [ [  1,  0,  0, -heightD],
                                        [  0,  1,  0,   widthD],
                                        [  0,  0,  1,  lengthD],
                                        [  0,  0,  0,        1] ] )
    # +135 around Y
    tfFRBaseInSpineBase = np.matrix( [ [ -0.707,  0,   0.707,  0],
                                       [      0,  1,       0,  0],
                                       [ -0.707,  0,  -0.707,  0],
                                       [      0,  0,       0,  1] ] )
    tfFRBaseInSpineBase *= np.matrix( [ [  1,  0,  0, -heightD],
                                        [  0,  1,  0,  -widthD],
                                        [  0,  0,  1,  lengthD],
                                        [  0,  0,  0,        1] ] )

    # +90 around X
    T = np.matrix( [ [  1,  0,  0,  0],
                     [  0,  0, -1,  0],
                     [  0,  1,  0,  0],
                     [  0,  0,  0,  1] ] )
    # +180 around Y
    tfRLBaseInSpineBase = T * np.matrix( [ [ -1,  0,  0,  0],
                                           [  0,  1,  0,  0],
                                           [  0,  0, -1,  0],
                                           [  0,  0,  0,  1] ] )
    tfRLBaseInSpineBase *= np.matrix( [ [  1,  0,  0,        0],
                                        [  0,  1,  0,   widthD],
                                        [  0,  0,  1, -lengthD],
                                        [  0,  0,  0,        1] ] )
    # +180 around Y
    tfRRBaseInSpineBase = T * np.matrix( [ [ -1,  0,  0,  0],
                                           [  0,  1,  0,  0],
                                           [  0,  0, -1,  0],
                                           [  0,  0,  0,  1] ] )
    tfRRBaseInSpineBase *= np.matrix( [ [  1,  0,  0,        0],
                                        [  0,  1,  0,  -widthD],
                                        [  0,  0,  1, -lengthD],
                                        [  0,  0,  0,        1] ] )

    global legs
    legs = [0, 0, 0, 0]
    angles = [0, 0, 0, 0, 0]
    sj = 1
    legs[0] = Leg( "FL", initLegJoints(sj), angles, tfFLBaseInSpineBase )
    sj += 5
    legs[1] = Leg( "FR", initLegJoints(sj), angles, tfFRBaseInSpineBase )
    sj += 5
    legs[2] = Leg( "RL", initLegJoints(sj), angles, tfRLBaseInSpineBase )
    sj += 5
    legs[3] = Leg( "RR", initLegJoints(sj), angles, tfRRBaseInSpineBase )


def initLegJoints(startingJoint):
    tmpTF = np.matrix( [ [  1,  0,  0,  0],
                         [  0,  1,  0,  0],
                         [  0,  0,  1,  0],
                         [  0,  0,  0,  1] ] )
    joints = [0, 0, 0, 0, 0, 0]
    for j in range(0, 5):
        joints[j] = Joint(startingJoint + j, tmpTF, tmpTF)
    joints[5] = Joint("F", tmpTF, tmpTF)  # Foot
    return joints


def rescale(old, oldMin, oldMax, newMin, newMax):
    oldRange = (oldMax - oldMin)
    newRange = (newMax - newMin)
    return (old - oldMin) * newRange / oldRange + newMin


def runSpineFK(spine, roll, pitch, yaw):
    # Spine front: In the future this can be controlled by e.g. orientation from IMU
    s = math.sin( math.radians(yaw) )
    c = math.cos( math.radians(yaw) )
    spine.tfSpineBaseInWorld = np.matrix( [ [  c, -s,  0,  0],
                                            [  s,  c,  0,  0],
                                            [  0,  0,  1,  0],
                                            [  0,  0,  0,  1] ] )

    s = math.sin( math.radians(pitch) )
    c = math.cos( math.radians(pitch) )
    spine.tfSpineBaseInWorld *= np.matrix( [ [  c,  0,  s,  0],
                                             [  0,  1,  0,  0],
                                             [ -s,  0,  c,  0],
                                             [  0,  0,  0,  1] ] )

    s = math.sin( math.radians(roll) )
    c = math.cos( math.radians(roll) )
    spine.tfSpineBaseInWorld *= np.matrix( [ [  1,  0,  0,  0],
                                             [  0,  c, -s,  0],
                                             [  0,  s,  c,  0],
                                             [  0,  0,  0,  1] ] )

    # TODO: Get this translation accurate e.g. at location of IMU
    # Translation (to get from world to robot spine)
    spine.tfSpineBaseInWorld *= np.matrix( [ [  1,  0,  0,  -50],
                                             [  0,  1,  0,     0],
                                             [  0,  0,  1,     0],
                                             [  0,  0,  0,     1] ] )

    # -45 around Y (to get from world to robot spine)
    spine.tfSpineBaseInWorld *= np.matrix( [ [  0.707,  0, -0.707,  0],
                                             [      0,  1,      0,  0],
                                             [  0.707,  0,  0.707,  0],
                                             [      0,  0,      0,  1] ] )

    d_1b = 16.975  # Dummy link offset

    s = [0, 0, 0, 0]
    c = [0, 0, 0, 0]
    for i in range(1, 4):
        s[i] = math.sin( math.radians(spine.angles[i-1]) )
        c[i] = math.cos( math.radians(spine.angles[i-1]) )

    tfJointInPrevJoint = [0, 0, 0]

    # Front spine joint
    tfJointInPrevJoint[0] = np.matrix( [ [ c[1], -s[1],  0,    0],
                                         [ s[1],  c[1],  0,    0],
                                         [    0,     0,  1,    0],
                                         [    0,     0,  0,    1] ] )

    # Dummy joint
    tfJointInPrevJoint[1] = np.matrix( [ [    1,     0,  0,    0],
                                         [    0,     1,  0,    0],
                                         [    0,     0,  1, d_1b],
                                         [    0,     0,  0,    1] ] )

    # Rear spine joint
    tfJointInPrevJoint[2] = np.matrix( [ [ c[3], -s[3],  0,    0],
                                         [    0,     0,  1,    0],
                                         [-s[3], -c[3],  0,    0],
                                         [    0,     0,  0,    1] ] )

    for j in range(0, 3):
        # Assign joint transforms, in preceeding joint coords and in world coords
        spine.joints[j].tfJointInPrevJoint = deepcopy(tfJointInPrevJoint[j])
        if j == 0:
            T = spine.tfSpineBaseInWorld
        else:
            T = spine.joints[j-1].tfJointInWorld
        spine.joints[j].tfJointInWorld = T * tfJointInPrevJoint[j]

    # Update legs
    for leg in legs:
        runLegFK(leg)


def runSpineIK():
    #TODO
    # ...
    #
    runSpineFK()


def runLegFK(leg):
    global a
    global footOffset

    a = [0, 0, 29.05, 76.919, 72.96, 45.032]  # Link lengths "a-1"

    footOffset = 33.596

    s = [0, 0, 0, 0, 0, 0]
    c = [0, 0, 0, 0, 0, 0]
    for i in range(1, 6):
        s[i] = math.sin( math.radians(leg.angles[i-1]) )
        c[i] = math.cos( math.radians(leg.angles[i-1]) )

    tfJointInPrevJoint = [0, 0, 0, 0, 0, 0]

    tfJointInPrevJoint[0] = np.matrix( [ [ c[1], -s[1],  0, a[1]],
                                         [ s[1],  c[1],  0,    0],
                                         [    0,     0,  1,    0],
                                         [    0,     0,  0,    1] ] )

    tfJointInPrevJoint[1] = np.matrix( [ [ c[2], -s[2],  0, a[2]],
                                         [    0,     0, -1,    0],
                                         [ s[2],  c[2],  0,    0],
                                         [    0,     0,  0,    1] ] )

    tfJointInPrevJoint[2] = np.matrix( [ [ c[3], -s[3],  0, a[3]],
                                         [ s[3],  c[3],  0,    0],
                                         [    0,     0,  1,    0],
                                         [    0,     0,  0,    1] ] )

    tfJointInPrevJoint[3] = np.matrix( [ [ c[4], -s[4],  0, a[4]],
                                         [ s[4],  c[4],  0,    0],
                                         [    0,     0,  1,    0],
                                         [    0,     0,  0,    1] ] )

    tfJointInPrevJoint[4] = np.matrix( [ [ c[5], -s[5],  0, a[5]],
                                         [    0,     0,  1,    0],
                                         [-s[5], -c[5],  1,    0],
                                         [    0,     0,  0,    1] ] )

    tfJointInPrevJoint[5] = np.matrix( [ [  1,  0,  0,  footOffset],
                                         [  0,  1,  0,  0],
                                         [  0,  0,  1,  0],
                                         [  0,  0,  0,  1] ] )

    for j in range(0, 6):
        # Assign joint transforms, in preceeding joint coords and in world coords
        leg.joints[j].tfJointInPrevJoint = deepcopy(tfJointInPrevJoint[j])
        if j == 0:
            if (leg.id == "FL") or (leg.id == "FR"):
                T = spine.tfSpineBaseInWorld * leg.tfLegBaseInSpineBase
            else:
                T = spine.joints[2].tfJointInWorld * leg.tfLegBaseInSpineBase
        else:
            T = leg.joints[j-1].tfJointInWorld
        leg.joints[j].tfJointInWorld = T * tfJointInPrevJoint[j]


def runLegIK(leg, target):
    # Convert target in world to be in leg base
    tfSpineBaseInLegBase = np.linalg.inv(leg.tfLegBaseInSpineBase)
    if (leg.id == "FL") or (leg.id == "FR"):
        T = spine.tfSpineBaseInWorld
        worldInSpineBase = np.linalg.inv(spine.tfSpineBaseInWorld)
    else:
        worldInSpineBase = np.linalg.inv(spine.joints[2].tfJointInWorld)
    targetInLegBase = tfSpineBaseInLegBase * worldInSpineBase * target

    # Solve Joint 1
    num = targetInLegBase[1, 3]
    den = abs(targetInLegBase[0, 3]) - footOffset
    a0Rads = math.atan2(num, den)
    leg.angles[0] = math.degrees(a0Rads)

    # Lengths projected onto z-plane
    c0 = math.cos(a0Rads)
    a2p = a[2]*c0
    a3p = a[3]*c0
    a4p = a[4]*c0
    a5p = a[5]*c0

    j4Height = abs(targetInLegBase[0, 3]) - a2p - a5p - footOffset

    j2j4DistSquared = math.pow(j4Height, 2) + math.pow(targetInLegBase[2, 3], 2)
    j2j4Dist = math.sqrt(j2j4DistSquared)

    # Solve Joint 2
    num = targetInLegBase[2, 3]
    den = j4Height
    psi = math.degrees( math.atan2(num, den) )

    num = math.pow(a3p, 2) + j2j4DistSquared - math.pow(a4p, 2)
    den = 2.0*a3p*j2j4Dist
    if abs(num) <= abs(den):
        phi = math.degrees( math.acos(num/den) )
        leg.angles[1] = - (phi - psi)

    # Solve Joint 3
    num = math.pow(a3p, 2) + math.pow(a4p, 2) - j2j4DistSquared
    den = 2.0*a3p*a4p
    if abs(num) <= abs(den):
        leg.angles[2] = 180.0 - math.degrees( math.acos(num/den) )

    # Solve Joint 4
    num = math.pow(a4p, 2) + j2j4DistSquared - math.pow(a3p, 2)
    den = 2.0*a4p*j2j4Dist
    if abs(num) <= abs(den):
        omega = math.degrees( math.acos(num/den) )
        leg.angles[3] = - (psi + omega)

    # Solve Joint 5
    leg.angles[4] = - leg.angles[0]

    runLegFK(leg)

    #print "target: ", target
    #print "targetInLegBase: ", targetInLegBase
    #print "leg.angles: ", leg.angles


def testIK():
    global tTIK
    global rateMsTIK
    tTIK = 2*math.pi
    rateMsTIK = 50
    root.after(rateMsTIK, testIKCallback)


def testIKCallback():
    global tTIK
    aEll = 60
    bEll = 20
    xAdjust = 0
    yAdjust = 30
    tTIK = tTIK - 0.1
    if tTIK >= 0:
        u = math.tan(tTIK/2.0)
        u2 = math.pow(u, 2)
        x = aEll*(1 - u2) / (u2 + 1)
        y = 2*bEll*u / (u2 + 1)
        target[0, 3] = targetHome[selectedLeg][0, 3] + x + xAdjust
        target[2, 3] = targetHome[selectedLeg][2, 3] + y + yAdjust
        runLegIK(legs[selectedLeg], target)
        root.after(rateMsTIK, testIKCallback)


def loadFromFile(filename):
    global FLUpDown
    global FLFwdBack
    global FRUpDown
    global FRFwdBack
    global RLUpDown
    global RLFwdBack
    global RRUpDown
    global RRFwdBack

    FLUpDown = []
    FLFwdBack = []
    FRUpDown = []
    FRFwdBack = []
    RLUpDown = []
    RLFwdBack = []
    RRUpDown = []
    RRFwdBack = []

    arraySize = 100
    rowOffset = 2
    amplAdjust = 50
    with open(filename, 'rb') as csvfile:
        reader = csv.reader(csvfile, delimiter=',')
        for r, row in enumerate(reader):
            if r in range(rowOffset, rowOffset + arraySize):
                #print r, row
                for c, col in enumerate(row):
                    if c in range(2, 10):
                        #print c, col
                        if c == 2:
                            FLUpDown.append(amplAdjust*float(col))
                        if c == 3:
                            FLFwdBack.append(amplAdjust*float(col))
                        if c == 4:
                            FRUpDown.append(amplAdjust*float(col))
                        if c == 5:
                            FRFwdBack.append(amplAdjust*float(col))
                        if c == 6:
                            RLUpDown.append(amplAdjust*float(col))
                        if c == 7:
                            RLFwdBack.append(amplAdjust*float(col))
                        if c == 8:
                            RRUpDown.append(amplAdjust*float(col))
                        if c == 9:
                            RRFwdBack.append(amplAdjust*float(col))
    csvfile.close()


def findClosestLegPose():
    minDist = 0
    idx = 0
    for i in range(0, len(FLUpDown)):

        distances = np.zeros(len(FLUpDown))
        minThresh = 20
        penalty = 10

        x = abs(currentPose[0] - FLUpDown[i])
        distances[0] = x
        if (x > minThresh):
            distances[0] += penalty

        x = abs(currentPose[1] - FLFwdBack[i])
        distances[1] = x
        if (x > minThresh):
            distances[1] += penalty

        x = abs(currentPose[2] - FRUpDown[i])
        distances[2] = x
        if (x > minThresh):
            distances[2] += penalty

        x = abs(currentPose[3] - FRFwdBack[i])
        distances[3] = x
        if (x > minThresh):
            distances[3] += penalty

        x = abs(currentPose[4] - RLUpDown[i])
        distances[4] = x
        if (x > minThresh):
            distances[4] += penalty

        x = abs(currentPose[5] - RLFwdBack[i])
        distances[5] = x
        if (x > minThresh):
            distances[5] += penalty

        x = abs(currentPose[6] - RRUpDown[i])
        distances[6] = x
        if (x > minThresh):
            distances[6] += penalty

        x = abs(currentPose[7] - RRFwdBack[i])
        distances[7] = x
        if (x > minThresh):
            distances[7] += penalty

        distanceMetric = 0
        for d in distances:
            distanceMetric += d

        if (i == 0) or (distanceMetric < minDist):
            minDist = distanceMetric
            idx = i

    #print "Current index:", iLT
    #print "Closest new index:", idx
    #print "Dist:", minDist
    #print "Index diff (abs):", abs(iLT - idx)

    return idx


def loadTargets1():
    # Load from csv
    loadFromFile("Gait_Creep.csv")

    # Run IK
    global iLT
    global rateMsLT
    global gaitCallbackRunning
    rateMsLT = 30
    if not 'gaitCallbackRunning' in globals():
        gaitCallbackRunning = False
    if not gaitCallbackRunning:
        iLT = 0
        root.after(rateMsLT, loadTargetsCallback)
    else:
        iLT = findClosestLegPose()


def loadTargets2():
    # Load from csv
    loadFromFile("Gait_Walk.csv")

    # Run IK
    global iLT
    global rateMsLT
    global gaitCallbackRunning
    rateMsLT = 30
    if not 'gaitCallbackRunning' in globals():
        gaitCallbackRunning = False
    if not gaitCallbackRunning:
        iLT = 0
        root.after(rateMsLT, loadTargetsCallback)
    else:
        iLT = findClosestLegPose()


def loadTargetsCallback():
    global showTarget
    global iLT
    global currentPose
    global gaitCallbackRunning
    showTarget = False
    xAdjust = -20
    zAdjust = 20
    #print "i: ", iLT

    if iLT < len(FLUpDown):
        # FL
        i = 0
        target[0, 3] = targetHome[i][0, 3] + FLFwdBack[iLT] + xAdjust
        target[1, 3] = targetHome[i][1, 3]
        target[2, 3] = targetHome[i][2, 3] + FLUpDown[iLT] + zAdjust
        runLegIK(legs[i], target)
        # FR
        i = 1
        target[0, 3] = targetHome[i][0, 3] + FRFwdBack[iLT] + xAdjust
        target[1, 3] = targetHome[i][1, 3]
        target[2, 3] = targetHome[i][2, 3] + FRUpDown[iLT] + zAdjust
        runLegIK(legs[i], target)
        # RL
        i = 2
        target[0, 3] = targetHome[i][0, 3] + RLFwdBack[iLT] + xAdjust
        target[1, 3] = targetHome[i][1, 3]
        target[2, 3] = targetHome[i][2, 3] + RLUpDown[iLT] + zAdjust
        runLegIK(legs[i], target)
        # RR
        i = 3
        target[0, 3] = targetHome[i][0, 3] + RRFwdBack[iLT] + xAdjust
        target[1, 3] = targetHome[i][1, 3]
        target[2, 3] = targetHome[i][2, 3] + RRUpDown[iLT] + zAdjust
        runLegIK(legs[i], target)

        currentPose = [ FLUpDown[iLT], FLFwdBack[iLT], FRUpDown[iLT], FRFwdBack[iLT],
                        RLUpDown[iLT], RLFwdBack[iLT], RRUpDown[iLT], RRFwdBack[iLT] ]

        iLT = iLT + 1
        gaitCallbackRunning = True
        root.after(rateMsLT, loadTargetsCallback)

    else:
        #print "Done"
        gaitCallbackRunning = False
        showTarget = True


def toggleJoystick():
    if jsVar.get() == 0:
        gamepadHandler.pause()
    else:
        gamepadHandler.resume()


def initViews():
    axisW = 4
    axisL = 60
    borderDist = 40

    # Side view axis widget
    sideViewCanvas.create_line( canvasW - (borderDist + axisL), borderDist + axisL, canvasW - borderDist, borderDist + axisL,
                                fill = "red", width = axisW, tag = "alwaysShown" )  # x-axis
    sideViewCanvas.create_text( canvasW - (borderDist + axisL), borderDist + axisL + 20, text = "X",
                                font = defaultFont, fill = "red", tag = "alwaysShown" )
    sideViewCanvas.create_line( canvasW - borderDist, borderDist, canvasW - borderDist, borderDist + axisL,
                                fill = "blue", width = axisW, tag = "alwaysShown" )  # z-axis
    sideViewCanvas.create_text( canvasW - borderDist + 20, borderDist, text = "Z",
                                font = defaultFont, fill = "blue", tag = "alwaysShown" )

    # Front view axis widget
    frontViewCanvas.create_line( canvasW - (borderDist + axisL), borderDist + axisL, canvasW - borderDist, borderDist + axisL,
                                 fill = "green", width = axisW, tag = "alwaysShown" )  # y-axis
    frontViewCanvas.create_text( canvasW - borderDist, borderDist + axisL + 20, text = "Y",
                                 font = defaultFont, fill = "green", tag = "alwaysShown" )
    frontViewCanvas.create_line( canvasW - (borderDist + axisL), borderDist, canvasW - (borderDist + axisL), borderDist + axisL,
                                 fill = "blue", width = axisW, tag = "alwaysShown" )  # z-axis
    frontViewCanvas.create_text( canvasW - (borderDist + axisL) - 20, borderDist, text = "Z",
                                 font = defaultFont, fill = "blue", tag = "alwaysShown" )

    # Top view axis widget
    topViewCanvas.create_line( canvasW - (borderDist + axisL), borderDist, canvasW - borderDist, borderDist,
                               fill = "red", width = axisW, tag = "alwaysShown" )  # x-axis
    topViewCanvas.create_text( canvasW - (borderDist + axisL), borderDist - 20, text = "X",
                               font = defaultFont, fill = "red", tag = "alwaysShown" )
    topViewCanvas.create_line( canvasW - borderDist, borderDist, canvasW - borderDist, borderDist + axisL,
                               fill = "green", width = axisW, tag = "alwaysShown" )  # y-axis
    topViewCanvas.create_text( canvasW - borderDist + 20, borderDist + axisL, text = "Y",
                               font = defaultFont, fill = "green", tag = "alwaysShown" )


def redraw():
    # Redraw views
    sideViewCanvas.delete("clear")
    frontViewCanvas.delete("clear")
    topViewCanvas.delete("clear")

    # Spine
    for j in range(0, 3, 2):  # Skip dummy joint
        drawJoint( spine.joints[j].id,
                   spine.joints[j].tfJointInWorld[0, 3],
                   spine.joints[j].tfJointInWorld[1, 3],
                   spine.joints[j].tfJointInWorld[2, 3] )

    # Legs
    for leg in legs:
        for j in range(0, 5):
            drawLink( leg.joints[j].tfJointInWorld[0, 3],
                      leg.joints[j].tfJointInWorld[1, 3],
                      leg.joints[j].tfJointInWorld[2, 3],
                      leg.joints[j+1].tfJointInWorld[0, 3],
                      leg.joints[j+1].tfJointInWorld[1, 3],
                      leg.joints[j+1].tfJointInWorld[2, 3] )
        for j in range(0, 5):
            drawJoint( leg.joints[j].id,
                       leg.joints[j].tfJointInWorld[0, 3],
                       leg.joints[j].tfJointInWorld[1, 3],
                       leg.joints[j].tfJointInWorld[2, 3] )
        drawEE( leg.joints[5].id,
                leg.joints[5].tfJointInWorld[0, 3],
                leg.joints[5].tfJointInWorld[1, 3],
                leg.joints[5].tfJointInWorld[2, 3] )

    # Target
    global showTarget
    if showTarget:
        drawTarget( target[0, 3],
                    target[1, 3],
                    target[2, 3],
                    speed )


def drawJoint(id, x, y, z):
    r = 25
    fillCol = "#FFFFE0"
    borderCol = "#00008B"
    w = 6
    sideViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                canvasW - canvasScale*x + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    sideViewCanvas.create_text( canvasW - canvasScale*x + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                text = id, font = ("Times", 12, "bold"), tag = "clear" )

    frontViewCanvas.create_oval( canvasW + canvasScale*y - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                 canvasW + canvasScale*y + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                 fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    frontViewCanvas.create_text( canvasW + canvasScale*y + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                 text = id, font = ("Times", 12, "bold"), tag = "clear" )

    topViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH + canvasScale*y - r + canvasOffset[2],
                               canvasW - canvasScale*x + r + canvasOffset[0], canvasH + canvasScale*y + r + canvasOffset[2],
                               fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    topViewCanvas.create_text( canvasW - canvasScale*x + canvasOffset[0], canvasH + canvasScale*y + canvasOffset[2],
                               text = id, font = ("Times", 12, "bold"), tag = "clear" )


def drawEE(id, x, y, z):
    r = 25
    fillCol = "#00008B"
    borderCol = "#00008B"
    w = 6
    sideViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                canvasW - canvasScale*x + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    sideViewCanvas.create_text( canvasW - canvasScale*x + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                text = id, fill = "white", font = ("Times", 12, "bold"), tag = "clear" )

    frontViewCanvas.create_oval( canvasW + canvasScale*y - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                 canvasW + canvasScale*y + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                 fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    frontViewCanvas.create_text( canvasW + canvasScale*y + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                 text = id, fill = "white", font = ("Times", 12, "bold"), tag = "clear" )

    topViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH + canvasScale*y - r + canvasOffset[2],
                               canvasW - canvasScale*x + r + canvasOffset[0], canvasH + canvasScale*y + r + canvasOffset[2],
                               fill = fillCol, outline = borderCol, width = w, tag = "clear" )
    topViewCanvas.create_text( canvasW - canvasScale*x + canvasOffset[0], canvasH + canvasScale*y + canvasOffset[2],
                               text = id, fill = "white", font = ("Times", 12, "bold"), tag = "clear" )


def drawLink(Ax, Ay, Az, Bx, By, Bz):
    fillCol = "#00008B"
    w = 10
    sideViewCanvas.create_line( canvasW - canvasScale*Ax + canvasOffset[0], canvasH - canvasScale*Az + canvasOffset[1],
                                canvasW - canvasScale*Bx + canvasOffset[0], canvasH - canvasScale*Bz + canvasOffset[1],
                                fill = fillCol, width = w, tag = "clear" )
    frontViewCanvas.create_line( canvasW + canvasScale*Ay + canvasOffset[0], canvasH - canvasScale*Az + canvasOffset[1],
                                 canvasW + canvasScale*By + canvasOffset[0], canvasH - canvasScale*Bz + canvasOffset[1],
                                 fill = fillCol, width = w, tag = "clear" )
    topViewCanvas.create_line( canvasW - canvasScale*Ax + canvasOffset[0], canvasH + canvasScale*Ay + canvasOffset[2],
                                canvasW - canvasScale*Bx + canvasOffset[0], canvasH + canvasScale*By + canvasOffset[2],
                                fill = fillCol, width = w, tag = "clear" )


def drawTarget(x, y, z, speed):
    r = 32
    borderCol = "#3D9140"
    w = 10
    # Target circle
    sideViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                canvasW - canvasScale*x + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                outline = borderCol, width = w, tag = "clear" )
    frontViewCanvas.create_oval( canvasW + canvasScale*y - r + canvasOffset[0], canvasH - canvasScale*z - r + canvasOffset[1],
                                 canvasW + canvasScale*y + r + canvasOffset[0], canvasH - canvasScale*z + r + canvasOffset[1],
                                 outline = borderCol, width = w, tag = "clear" )
    topViewCanvas.create_oval( canvasW - canvasScale*x - r + canvasOffset[0], canvasH + canvasScale*y - r + canvasOffset[2],
                               canvasW - canvasScale*x + r + canvasOffset[0], canvasH + canvasScale*y + r + canvasOffset[2],
                               outline = borderCol, width = w, tag = "clear" )
    # Speed vector
    fillCol = borderCol
    sx = speed[0]
    sy = speed[1]
    sz = speed[2]
    k = 1000.0 / inputForceMax  # Arbitrary scaling, to make max. length of vector constant
    sideViewCanvas.create_line( canvasW - canvasScale*x + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                canvasW - canvasScale*x - sx*k + canvasOffset[0],
                                canvasH - canvasScale*z - sz*k + canvasOffset[1],
                                fill = fillCol, width = w, tag = "clear" )
    frontViewCanvas.create_line( canvasW + canvasScale*y + canvasOffset[0], canvasH - canvasScale*z + canvasOffset[1],
                                 canvasW + canvasScale*y + sy*k + canvasOffset[0],
                                 canvasH - canvasScale*z - sz*k + canvasOffset[1],
                                 fill = fillCol, width = w, tag = "clear" )
    topViewCanvas.create_line( canvasW - canvasScale*x + canvasOffset[0], canvasH + canvasScale*y + canvasOffset[2],
                               canvasW - canvasScale*x - sx*k + canvasOffset[0],
                               canvasH + canvasScale*y + sy*k + canvasOffset[2],
                               fill = fillCol, width = w, tag = "clear" )


def selectLeg():
	global selectedLeg
	selectedLeg = rbLegVar.get()


def joint1SliderCallback(val):
    legs[selectedLeg].angles[0] = float(val)
    runLegFK(legs[selectedLeg])


def joint2SliderCallback(val):
    legs[selectedLeg].angles[1] = float(val)
    runLegFK(legs[selectedLeg])


def joint3SliderCallback(val):
    legs[selectedLeg].angles[2] = float(val)
    runLegFK(legs[selectedLeg])


def joint4SliderCallback(val):
    legs[selectedLeg].angles[3] = float(val)
    runLegFK(legs[selectedLeg])


def joint5SliderCallback(val):
    legs[selectedLeg].angles[4] = float(val)
    runLegFK(legs[selectedLeg])


def targetXSliderCallback(val):
    target[0, 3] = targetHome[selectedLeg][0, 3] + float(val)
    target[1, 3] = targetHome[selectedLeg][1, 3] + float(targetYSlider.get())
    target[2, 3] = targetHome[selectedLeg][2, 3] + float(targetZSlider.get())
    runLegIK(legs[selectedLeg], target)


def targetYSliderCallback(val):
    target[0, 3] = targetHome[selectedLeg][0, 3] + float(targetXSlider.get())
    target[1, 3] = targetHome[selectedLeg][1, 3] + float(val)
    target[2, 3] = targetHome[selectedLeg][2, 3] + float(targetZSlider.get())
    runLegIK(legs[selectedLeg], target)


def targetZSliderCallback(val):
    target[0, 3] = targetHome[selectedLeg][0, 3] + float(targetXSlider.get())
    target[1, 3] = targetHome[selectedLeg][1, 3] + float(targetYSlider.get())
    target[2, 3] = targetHome[selectedLeg][2, 3] + float(val)
    runLegIK(legs[selectedLeg], target)


def targetRollSliderCallback(val):
#    target[3] = targetHome[selectedLeg][3] + float(val)
#    target[4] = targetHome[selectedLeg][4] + float(targetPitchSlider.get())
#    target[5] = targetHome[selectedLeg][5] + float(targetYawSlider.get())
    runLegIK(legs[selectedLeg], target)


def targetPitchSliderCallback(val):
#    target[3] = targetHome[selectedLeg][3] + float(targetRollSlider.get())
#    target[4] = targetHome[selectedLeg][4] + float(val)
#    target[5] = targetHome[selectedLeg][5] + float(targetYawSlider.get())
    runLegIK(legs[selectedLeg], target)


def targetYawSliderCallback(val):
#    target[3] = targetHome[selectedLeg][3] + float(targetRollSlider.get())
#    target[4] = targetHome[selectedLeg][4] + float(targetPitchSlider.get())
#    target[5] = targetHome[selectedLeg][5] + float(val)
    runLegIK(legs[selectedLeg], target)


def spineRollSliderCallback(val):
    r = float(val)
    p = spinePitchSlider.get()
    y = spineYawSlider.get()
    runSpineFK(spine, r, p, y)


def spinePitchSliderCallback(val):
    r = spineRollSlider.get()
    p = float(val)
    y = spineYawSlider.get()
    runSpineFK(spine, r, p, y)


def spineYawSliderCallback(val):
    r = spineRollSlider.get()
    p = spinePitchSlider.get()
    y = float(val)
    runSpineFK(spine, r, p, y)


def spineJoint1SliderCallback(val):
    r = spineRollSlider.get()
    p = spinePitchSlider.get()
    y = spineYawSlider.get()
    spine.angles[0] = float(val)
    runSpineFK(spine, r, p, y)


def spineJoint2SliderCallback(val):
    r = spineRollSlider.get()
    p = spinePitchSlider.get()
    y = spineYawSlider.get()
    spine.angles[2] = float(val)
    runSpineFK(spine, r, p, y)


def messageBoxModifiedCallback(self):
    messageBox.see(END)
    messageBox.edit_modified(False)


def logMessage(msg):
    messageBox.insert(END, msg + "\n")


def quit():
    serialHandler.closeSerial()
    gamepadReader.stop()
    gamepadHandler.stop()
    serialHandler.stop()
    # Wait for threads to finish
    #print threading.active_count()
    while gamepadReader.isAlive() or gamepadHandler.isAlive() or serialHandler.isAlive():
        #print "waiting"
        sleep(0.1)
    #print threading.active_count()
    root.destroy()


global sideViewCanvas, frontViewCanvas, topViewCanvas
global canvasW, canvasH
global canvasScale, canvasOffset
global targetXSlider, targetYSlider, targetZSlider

startTime = strftime("%a, %d %b %Y %H:%M:%S", localtime())

root = Tk()
root.title("Quadbot 17 Kinematics")
rootWidth = 2600
rootHeight = 1660
root.geometry("%dx%d" % (rootWidth, rootHeight))


# Scaling for 4K screens
root.tk.call('tk', 'scaling', 4.0)
defaultFont = ("System", 12)


Grid.rowconfigure(root, 0, weight=1)
Grid.columnconfigure(root, 0, weight=1)

sideViewFrame = Frame(root)
topViewFrame = Frame(root)
frontViewFrame = Frame(root)
controlsFrame = Frame(root)

sideViewFrame.grid(row=0, column=0, sticky=N+W)
frontViewFrame.grid(row=0, column=1, sticky=N+E)
topViewFrame.grid(row=1, column=0, sticky=S+W)
controlsFrame.grid(row=1, column=1, sticky=S+E)

canvasW = 1170
canvasH = 760

canvasScale = 2  # 1 mm -> 2 pixels
canvasOffset = [-canvasW/2, -canvasH + 200, -canvasH + 370]  # 3rd offset is for top view only

sideViewLabel = Label(sideViewFrame, text="Side View", font = defaultFont)
sideViewLabel.grid(row=0, column=0)
sideViewCanvas = Canvas(sideViewFrame, background="#E0FFFF", width = canvasW, height = canvasH)
sideViewCanvas.grid(row=1, column=0, sticky=N+S+W+E)

frontViewLabel = Label(frontViewFrame, text="Front View", font = defaultFont)
frontViewLabel.grid(row=0, column=0)
frontViewCanvas = Canvas(frontViewFrame, background="#FFFACD", width = canvasW, height = canvasH)
frontViewCanvas.grid(row=1, column=0, sticky=N+S+W+E)

topViewLabel = Label(topViewFrame, text="Top View", font = defaultFont)
topViewLabel.grid(row=0, column=0)
topViewCanvas = Canvas(topViewFrame, background="#E0EEE0", width = canvasW, height = canvasH)
topViewCanvas.grid(row=1, column=0, sticky=N+S+W+E)

controlsSubFrame = Frame(controlsFrame)
buttonsFrame = Frame(controlsFrame)

messageBoxFrame = Frame(controlsSubFrame)
selectFrame = Frame(controlsSubFrame)
jointSlidersFrame = Frame(controlsSubFrame)
targetSlidersFrame = Frame(controlsSubFrame)
spineSlidersFrame = Frame(controlsSubFrame)

messageBoxFrame.grid(row=0, column=0, sticky=N)
selectFrame.grid(row=0, column=1, sticky=N)
jointSlidersFrame.grid(row=0, column=2, sticky=N)
targetSlidersFrame.grid(row=0, column=3, sticky=N)
spineSlidersFrame.grid(row=0, column=4, sticky=N)

legSelectSubFrame = Frame(selectFrame)
legSelectSubFrame.grid(row=0, column=0, sticky=N)

controlsSubFrame.grid(row=0, column=0, sticky=N)
buttonsFrame.grid(row=1, column=0, sticky=N)

messageBox = Text(messageBoxFrame, width = 32, height=18, font = defaultFont)
messageBox.grid(row=0, column=0, sticky=N+S+W+E)
scrl = Scrollbar(messageBoxFrame, command=messageBox.yview)
scrl.grid(row=0, column=1, sticky=N+S)
messageBox.config(yscrollcommand=scrl.set)
messageBox.bind("<<Modified>>", messageBoxModifiedCallback)
logMessage("Started at: " + startTime)


legSelectLabel = Label(legSelectSubFrame, text="Leg", font = defaultFont)
legSelectLabel.grid(row=0, column=0)

rbLegVar = IntVar()
FLRadiobutton = Radiobutton( legSelectSubFrame, text = "FL", font = defaultFont, variable = rbLegVar,
                             value = 0, command = selectLeg )
FRRadiobutton = Radiobutton( legSelectSubFrame, text = "FR", font = defaultFont, variable = rbLegVar,
                             value = 1, command = selectLeg )
RLRadiobutton = Radiobutton( legSelectSubFrame, text = "RL", font = defaultFont, variable = rbLegVar,
                             value = 2, command = selectLeg )
RRRadiobutton = Radiobutton( legSelectSubFrame, text = "RR", font = defaultFont, variable = rbLegVar,
                             value = 3, command = selectLeg )
FLRadiobutton.grid(row=1, column=0)
FRRadiobutton.grid(row=2, column=0)
RLRadiobutton.grid(row=3, column=0)
RRRadiobutton.grid(row=4, column=0)
FLRadiobutton.select()  # Set default


fkLabel = Label(jointSlidersFrame, text="FK - Joints", font = defaultFont)
fkLabel.grid(row=0, column=0)

jsRange = 180.0
joint1Slider = Scale( jointSlidersFrame, from_ = -jsRange, to = jsRange, resolution = 0.1, label = "j1",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = joint1SliderCallback )
joint1Slider.grid(row=1, column=0)

joint2Slider = Scale( jointSlidersFrame, from_ = -jsRange, to = jsRange, resolution = 0.1, label = "j2",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = joint2SliderCallback )
joint2Slider.grid(row=2, column=0)

joint3Slider = Scale( jointSlidersFrame, from_ = -jsRange, to = jsRange, resolution = 0.1, label = "j3",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = joint3SliderCallback )
joint3Slider.grid(row=3, column=0)

joint4Slider = Scale( jointSlidersFrame, from_ = -jsRange, to = jsRange, resolution = 0.1, label = "j4",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = joint4SliderCallback )
joint4Slider.grid(row=4, column=0)

joint5Slider = Scale( jointSlidersFrame, from_ = -jsRange, to = jsRange, resolution = 0.1, label = "j5",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = joint5SliderCallback )
joint5Slider.grid(row=5, column=0)


ikLabel = Label(targetSlidersFrame, text="IK - Target", font = defaultFont)
ikLabel.grid(row=0, column=0)

tsRange = 300.0
targetXSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 1.0, label = "X",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetXSliderCallback )
targetXSlider.grid(row=1, column=0)

targetYSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 1.0, label = "Y",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetYSliderCallback )
targetYSlider.grid(row=2, column=0)

targetZSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 1.0, label = "Z",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetZSliderCallback )
targetZSlider.grid(row=3, column=0)

tsRange = 90.0
targetRollSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Roll",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetRollSliderCallback )
targetRollSlider.grid(row=4, column=0)

targetPitchSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Pitch",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetPitchSliderCallback )
targetPitchSlider.grid(row=5, column=0)

targetYawSlider = Scale( targetSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Yaw",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = targetYawSliderCallback )
targetYawSlider.grid(row=6, column=0)


rpyLabel = Label(spineSlidersFrame, text="Spine", font = defaultFont)
rpyLabel.grid(row=0, column=0)

tsRange = 180.0
spineRollSlider = Scale( spineSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Roll",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = spineRollSliderCallback )
spineRollSlider.grid(row=1, column=0)

spinePitchSlider = Scale( spineSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Pitch",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = spinePitchSliderCallback )
spinePitchSlider.grid(row=2, column=0)

spineYawSlider = Scale( spineSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "Yaw",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = spineYawSliderCallback )
spineYawSlider.grid(row=3, column=0)

tsRange = 180.0
spineJoint1Slider = Scale( spineSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "j1",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = spineJoint1SliderCallback )
spineJoint1Slider.grid(row=4, column=0)

spineJoint2Slider = Scale( spineSlidersFrame, from_ = -tsRange, to = tsRange, resolution = 0.1, label = "j2",
                      length = 200, width = 40, font = ("System", 9), orient=HORIZONTAL, command = spineJoint2SliderCallback )
spineJoint2Slider.grid(row=5, column=0)


jsVar = IntVar()
joystickCheckButton = Checkbutton(buttonsFrame, text="Joystick", var=jsVar, command=toggleJoystick, font = defaultFont)
joystickCheckButton.grid(row=0, column=0)
#joystickCheckButton.select()  # Set default

testIKButton = Button(buttonsFrame, text="Test IK", command=testIK, font = defaultFont)
testIKButton.grid(row=0, column=1)

loadTargets1Button = Button(buttonsFrame, text="Load 1", command=loadTargets1, font = defaultFont)
loadTargets1Button.grid(row=0, column=2)

loadTargets2Button = Button(buttonsFrame, text="Load 2", command=loadTargets2, font = defaultFont)
loadTargets2Button.grid(row=0, column=3)

quitButton = Button(buttonsFrame, text="Quit", command=quit, font = defaultFont)
quitButton.grid(row=0, column=4)


if __name__ == '__main__':
    global selectedLeg
    global spineAngleOffsets
    global legAngleOffsets
    global targetHome, target, speed
    global showTarget
    initSpine()
    initLegs()
    initViews()
    selectedLeg = 0

    # Offsets for natural "home" position
    spineAngleOffsets = [0, 0, -45]
    legAngleOffsets = [0, -34, 67.5, -33.5, 0]

    spine.angles = deepcopy(spineAngleOffsets)
    runSpineFK(spine, 0, 0, 0)
    spineJoint1Slider.set(spine.angles[0])
    spineJoint2Slider.set(spine.angles[2])

    for leg in legs:
        leg.angles = deepcopy(legAngleOffsets)
        runLegFK(leg)
    joint1Slider.set(legs[selectedLeg].angles[0])
    joint2Slider.set(legs[selectedLeg].angles[1])
    joint3Slider.set(legs[selectedLeg].angles[2])
    joint4Slider.set(legs[selectedLeg].angles[3])
    joint5Slider.set(legs[selectedLeg].angles[4])

    # Targets: Foot in world
    targetHome = [0, 0, 0, 0]
    for i in range (0,4):
        targetHome[i] = deepcopy(legs[i].joints[5].tfJointInWorld)
    target = deepcopy(targetHome[0])
    speed = [0, 0, 0]
    showTarget = True

    global inputLJSX
    inputLJSX = 0
    global inputLJSY
    inputLJSY = 0
    global inputRJSX
    inputRJSX = 0
    global inputRJSY
    inputRJSY = 0

    global inputForceMax, dragForceCoef
    inputForceMax = 2000
    dragForceCoef = 10

    gamepadReader = GamepadReader(root)
    gamepadReader.start()

    gamepadHandler = GamepadHandler(root)
    gamepadHandler.start()

    serialHandler = SerialHandler(root)
    serialHandler.start()

    App(root)
    root.mainloop()
