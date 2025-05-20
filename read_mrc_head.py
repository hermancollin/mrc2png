import argparse
import mrcfile
from pathlib import Path
import csv


def lookup(file: Path):
    with mrcfile.open(file) as mrc:
        # assuming isotropic px size
        px_size = float(mrc.voxel_size.x) / 10000  # Angstrom to um
        h = int(mrc.header.nx)
        w = int(mrc.header.ny)
    return file.stem, px_size, h, w

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True, help="path to input .mrc file or directory")

    args = vars(ap.parse_args())
    path = Path(args["input"])

    if path.is_dir():
        # instantiate a CSV file to write pixel sizes
        header_data = [('filename', 'px_size_x (um/px)', 'height (px)', 'width (px)')]
        for file in path.rglob('*.mrc'):
            row = lookup(file)
            header_data.append(row)
            print(row)

        with open(path / 'header_data.csv', 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            for data in header_data:
                writer.writerow(data)
    else:
        print(lookup(path))

if __name__ == '__main__':
    main()