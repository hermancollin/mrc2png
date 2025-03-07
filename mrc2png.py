'''
This script is used to convert individual .mrc images or a batch into PNGs. 
Additionnally, it allows conversion to 8-bits, resizing and normalization.

MRC is a standard fileformat for cryoEM and EM files.
'''

import argparse
import mrcfile
import cv2
import numpy as np
from pathlib import Path

# native JEM-1400 SerialEM 8000x resolution is 0.001412 um/px
# to match SRF data's resolution of 0.00493, we need this conversion rate
REDUCTION_FACTOR = 3.5

def convert(file):
    with mrcfile.open(file) as mrc:
        img = mrc.data
        h = header = mrc.header.nx
        w = header = mrc.header.ny
        d = header = mrc.header.nz
        print(f"{file.stem} is being converted. Shape: {h}x{w}x{d}, Voxel size [Angstrom]: {mrc.voxel_size}")
        # assuming isotropic px size
        px_size = mrc.voxel_size.x / 10000 # Angstrom to um
        # format px_size to 4 significant digits
        print(f'Pixel size will change from {px_size:.4g} um/px to {(px_size*REDUCTION_FACTOR):.4g} um/px')
    img = img.astype(np.float32)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    img = img.astype(np.uint8)
    reduced_size = (int(h//REDUCTION_FACTOR), int(w//REDUCTION_FACTOR))
    img = cv2.resize(img, reduced_size, interpolation=cv2.INTER_CUBIC)
    equalized = cv2.equalizeHist(img)
    output = file.parent / f"{file.stem}.png"
    cv2.imwrite(output, equalized)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True, help="path to input .mrc file or directory")

    args = vars(ap.parse_args())
    path = Path(args["input"])
    if path.is_dir():
        for file in path.rglob('*.mrc'):
            convert(file)
    else:
        convert(path)

if __name__ == '__main__':
    main()