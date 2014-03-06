from __future__ import print_function, division
from P4_sandbox import helper_functions as hf
import urllib
import shutil
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mplimg
from itertools import cycle
from matplotlib.patches import Ellipse
import sys

data_root = '/Users/maye/data/planet4'

def data_munging(img_id):
    print("Reading current marked data.")
    data = pd.read_hdf(os.path.join(data_root, 'marked.h5'),'df',
                       where='image_id=='+img_id)
    print("Done.")
    return data


def get_blotches(data, img_id):
    """get the blotches for given image_id"""
    subframe = data[data.image_id == img_id]
    blotches = subframe[subframe.marking == 'blotch']
    return blotches


def get_image_name_from_data(data):
    """This assumes that all data is for the same image!"""
    line = data.iloc[0]
    return os.path.basename(line.image_url.strip())

def get_image_from_record(line):
    """Download image if not there yet and return numpy array.
    
    Takes a data record (called 'line'), picks out the image_url.
    First checks if the name of that image is already stored in 
    the image path. If not, it grabs it from the server.
    Then uses matplotlib.image to read the image into a numpy-array
    and finally returns it.
    """
    url = line.image_url
    targetpath = os.path.join(data_root, 'images', os.path.basename(url))
    if not os.path.exists(targetpath):
        print("Did not find image in cache. Downloading ...")
        sys.stdout.flush()
        path = urllib.urlretrieve(url)[0]
        print("Done.")
        shutil.move(path, targetpath)
    else:
        print("Found image in cache.")
    im = mplimg.imread(targetpath)
    return im


def plot_blotches(data, img_id):
    blotches = get_blotches(data, img_id)
    # this will endlessly cycle through the colors given
    colors = cycle('bgrcmyk')
    fig, ax = plt.subplots(ncols=2)
    ax[0].imshow(get_image_from_record(blotches.iloc[0]))
    ax[1].imshow(get_image_from_record(blotches.iloc[0]))
    for i, color in zip(xrange(len(blotches),), colors):
        line = blotches.iloc[i]
        plt.scatter(line.x, line.y, color=color)
        el = Ellipse((line.x, line.y),
                 line.radius_1, line.radius_2, line.angle,
                 fill=False, color=color, linewidth=1)
        ax[1].add_artist(el)
    ax[0].set_title('image_id {}'.format(img_id))
    plt.savefig('plots/blotches_'+get_image_name_from_data(blotches))
    plt.show()


if __name__ == '__main__':
    # img id should be given on command line
    img_id = sys.argv[1]
    data = data_munging(img_id)
    main(data, img_id)
