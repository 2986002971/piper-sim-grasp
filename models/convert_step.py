#!/usr/bin/env python3
"""Convert a STEP solid to a binary STL while preserving its millimeter coordinates.

Requires cadquery-ocp. The resulting STL remains in STEP millimeters; the URDF
applies the explicit 0.001 scale to convert to meters.
"""
from pathlib import Path
import argparse

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.StlAPI import StlAPI_Writer
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", type=Path)
    parser.add_argument("stl", type=Path)
    parser.add_argument("--linear-deflection-mm", type=float, default=0.1)
    parser.add_argument("--angular-deflection-deg", type=float, default=8.0)
    args = parser.parse_args()
    if args.linear_deflection_mm <= 0 or args.angular_deflection_deg <= 0:
        parser.error("mesh deflections must be positive")

    reader = STEPControl_Reader()
    if reader.ReadFile(str(args.step)) != IFSelect_RetDone:
        raise SystemExit(f"Could not read STEP: {args.step}")
    if reader.TransferRoots() == 0:
        raise SystemExit("STEP contains no transferable roots")
    shape = reader.OneShape()
    if shape.IsNull():
        raise SystemExit("STEP produced an empty shape")

    BRepMesh_IncrementalMesh(shape, args.linear_deflection_mm, False, args.angular_deflection_deg * 3.141592653589793 / 180.0, True)
    args.stl.parent.mkdir(parents=True, exist_ok=True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(args.stl)):
        raise SystemExit(f"Could not write STL: {args.stl}")

    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    print(f"Wrote {args.stl}")
    print(f"STEP bounds (mm): {bounds.Get()}")
    print("Apply mesh scale 0.001 in URDF to convert the coordinates to meters.")


if __name__ == "__main__":
    main()
