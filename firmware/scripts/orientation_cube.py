#!/usr/bin/env python3
"""3D cube that mirrors the real-time orientation of the GY-271 + MPU-6050 sensors.

Run on the Raspberry Pi with a desktop environment:
    cd ~/Tooes && python3 firmware/scripts/orientation_cube.py

Requires: pygame, PyOpenGL, smbus2 (all pre-installed on the Pi).
"""

import sys
import math
import time

import pygame
from pygame.locals import DOUBLEBUF, OPENGL, QUIT, KEYDOWN, K_ESCAPE
from OpenGL.GL import (
    glClear, glClearColor, glEnable, glMatrixMode, glLoadIdentity,
    glTranslatef, glRotatef, glBegin, glEnd, glColor3f, glVertex3fv,
    glLineWidth,
    GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
    GL_MODELVIEW, GL_PROJECTION, GL_QUADS, GL_LINES,
)
from OpenGL.GLU import gluPerspective

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from firmware.hal.qmc5883l import QMC5883LRotationReader
from firmware.hal.mpu6050 import MPU6050TiltReader

VERTICES = (
    ( 1,  1, -1), ( 1, -1, -1), (-1, -1, -1), (-1,  1, -1),
    ( 1,  1,  1), ( 1, -1,  1), (-1, -1,  1), (-1,  1,  1),
)

FACES = (
    (0, 1, 2, 3),  # back
    (4, 5, 6, 7),  # front
    (0, 1, 5, 4),  # right
    (2, 3, 7, 6),  # left
    (0, 3, 7, 4),  # top
    (1, 2, 6, 5),  # bottom
)

FACE_COLORS = (
    (0.8, 0.2, 0.2),  # red
    (0.2, 0.8, 0.2),  # green
    (0.2, 0.2, 0.8),  # blue
    (0.8, 0.8, 0.2),  # yellow
    (0.8, 0.2, 0.8),  # magenta
    (0.2, 0.8, 0.8),  # cyan
)

EDGES = (
    (0,1),(1,2),(2,3),(3,0),
    (4,5),(5,6),(6,7),(7,4),
    (0,4),(1,5),(2,6),(3,7),
)


def draw_cube():
    glBegin(GL_QUADS)
    for i, face in enumerate(FACES):
        glColor3f(*FACE_COLORS[i])
        for vertex in face:
            glVertex3fv(VERTICES[vertex])
    glEnd()

    glLineWidth(2.0)
    glBegin(GL_LINES)
    glColor3f(0.0, 0.0, 0.0)
    for edge in EDGES:
        for vertex in edge:
            glVertex3fv(VERTICES[vertex])
    glEnd()


def main():
    tilt = MPU6050TiltReader()
    compass = QMC5883LRotationReader(tilt=tilt)

    pygame.init()
    screen = pygame.display.set_mode((640, 480), DOUBLEBUF | OPENGL)
    pygame.display.set_caption("Orientation Cube")

    glClearColor(0.15, 0.15, 0.15, 1.0)
    glEnable(GL_DEPTH_TEST)
    glMatrixMode(GL_PROJECTION)
    gluPerspective(45, 640 / 480, 0.1, 50.0)
    glMatrixMode(GL_MODELVIEW)

    clock = pygame.time.Clock()

    print("Orientation cube running. Close window or press Esc to quit.")
    print(f"{'heading':>10s}  {'pitch':>8s}  {'roll':>8s}")
    print("-" * 32)

    try:
        while True:
            for event in pygame.event.get():
                if event.type == QUIT:
                    return
                if event.type == KEYDOWN and event.key == K_ESCAPE:
                    return

            heading = compass.read_azimuth()
            pitch, roll = tilt.read_pitch_roll()

            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            glTranslatef(0.0, 0.0, -7.0)

            glRotatef(-pitch, 1, 0, 0)
            glRotatef(roll, 0, 0, 1)
            glRotatef(-heading, 0, 1, 0)

            draw_cube()

            pygame.display.flip()

            title = f"Heading: {heading:.0f}  Pitch: {pitch:.0f}  Roll: {roll:.0f}"
            pygame.display.set_caption(title)
            print(f"{heading:10.1f}  {pitch:8.1f}  {roll:8.1f}", flush=True)

            clock.tick(30)

    except KeyboardInterrupt:
        pass
    finally:
        compass.close()
        tilt.close()
        pygame.quit()
        print("Done.")


if __name__ == "__main__":
    main()
