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

NATIVE_MODEL_RESOLUTION = 0.00493 # um/px
# native JEM-1400 SerialEM 8000x resolution is 0.001412 um/px
# to match SRF data's resolution of 0.00493, we need this conversion rate
REDUCTION_FACTOR = 3.5

def convert(file, use_default_reduction_factor=False):
    with mrcfile.open(file) as mrc:
        img = mrc.data
        h = mrc.header.nx
        w = mrc.header.ny
        d = mrc.header.nz
        print(f"{file.stem} is being converted. Shape: {h}x{w}x{d}, Voxel size [Angstrom]: {mrc.voxel_size}")
        # assuming isotropic px size
        px_size = mrc.voxel_size.x / 10000 # Angstrom to um
        if use_default_reduction_factor:
            reduction = REDUCTION_FACTOR
        else:
            if px_size > NATIVE_MODEL_RESOLUTION:
                reduction = 1
                print(f'Pixel size larger than native model resolution. Image will not be upsampled.\n\t> {file.name}')
            else:
                reduction = NATIVE_MODEL_RESOLUTION / px_size
                print(f'Pixel size will change from {px_size:.4g} um/px to {(px_size*reduction):.4g} um/px')

    # normalization
    img = img.astype(np.float32)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    img = img.astype(np.uint8)
    # resize
    if reduction != 1:
        reduced_size = (int(h//reduction), int(w//reduction))
        img = cv2.resize(img, reduced_size, interpolation=cv2.INTER_CUBIC)
    # histogram equalization
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