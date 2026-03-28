# Experiments And Proof-Of-Concepts

This repository contains both product-facing code and experimental utilities.
This document draws the current boundary so the main documentation can stay
clear about what is core and what is exploratory.

## Core Runtime Code

These areas are part of the main project surface today:

- `firmware/hal/`
- `firmware/ui/`
- `firmware/opencellid.py`
- `firmware/run.py`
- `firmware/tests/`
- `scripts/setup_pi.sh`

These files support the Raspberry Pi app, the RF path, and the documented HAL
backends.

## Proof-Of-Concept And Support Scripts

These scripts are useful, but they are better understood as supporting tools or
experiments than as stable product entry points.

### `firmware/scripts/sweep_poc.py`

Purpose:

- walking proof-of-concept for motion tracking plus cell sampling

Status:

- actively meaningful for experimentation
- not the same as the e-paper app

### `firmware/scripts/orientation_cube.py`

Purpose:

- visualize sensor orientation on a Pi desktop

Status:

- calibration and debugging aid
- not part of the main end-user flow

### `firmware/scripts/install_tiles.py`

Purpose:

- preload offline map tiles for the map renderer

Status:

- operational utility
- required only if you want real offline tiles instead of placeholders

## `separate_component_files/`

The `separate_component_files/` directory should currently be treated as
experimental and component-specific.

Contents include:

- `hardcoded_PoC_display_ink.py`
- `mpu6050_accel_logger.py`
- `plot_mpu6050_imu_log.py`
- `plot_mpu6050_relative_path.py`

These files are valuable because they preserve experiments and one-off
validation work, but they should not be confused with the supported project API
or the primary runtime path.

## Why This Boundary Matters

Keeping experiments explicitly labeled has two benefits:

1. the project docs can stay honest about what a new developer or judge should
   run first
2. the exploratory work remains documented without being mistaken for stable
   product behavior

## Recommended Documentation Rule

When a file is experimental, document it in terms of:

- what question it helped answer
- what hardware or data it assumes
- whether its results have been folded into the main firmware path

When a file is core, document it in terms of:

- public behavior
- configuration
- algorithmic assumptions
- runtime dependencies
