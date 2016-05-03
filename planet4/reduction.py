#!/usr/bin/env python
from __future__ import division, print_function

import argparse
import logging
import os
import sys
import time

import pandas as pd
from ipyparallel import Client
from pathlib import Path

from . import markings
from .io import DBManager, data_root

try:
    from odo import odo
except ImportError:
    pass


logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)

# the split trick creates lists when u don't want to break ur fingers with
# typing ',,'','',',,' all the time...
blotch_data_cols = 'x y image_x image_y radius_1 radius_2'.split()
fan_data_cols = 'x y image_x image_y distance angle spread'.split()

analysis_cols = ['classification_id',
                 'created_at',
                 'image_id',
                 'image_name',
                 'image_url',
                 'user_name',
                 'marking',
                 'x_tile',
                 'y_tile',
                 'acquisition_date',
                 'local_mars_time',
                 'x',
                 'y',
                 'image_x',
                 'image_y',
                 'radius_1',
                 'radius_2',
                 'distance',
                 'angle',
                 'spread',
                 'version']

data_columns = ['classification_id', 'image_id',
                'image_name', 'user_name', 'marking',
                'acquisition_date', 'local_mars_time']


def filter_data(df):
    """scan for incomplete data and remove from dataframe.

    This only will work if missing data has been converted to np.NANs.
    """

    # split data into this marking and NOT this marking.
    fans = df[df.marking == 'fan']
    blotches = df[df.marking == 'blotch']
    rest = df[(df.marking != 'fan') & (df.marking != 'blotch')]

    # first drop incomplete data
    fans = fans.dropna(how='any', subset=fan_data_cols)
    blotches = blotches.dropna(how='any', subset=blotch_data_cols)

    # now filter for default data
    eps = 0.00001
    bzero_filter = (blotches.x.abs() < eps) & (blotches.y.abs() < eps)
    fzero_filter = (fans.x.abs() < eps) & (fans.y.abs() < eps)
    rest_zero_filter = (rest.x.abs() < eps) & (rest.y.abs() < eps)
    blotch_defaults = ((blotches.radius_1 - 10) < eps) & ((blotches.radius_2 - 10).abs() < eps)
    fan_defaults = (fans.angle.abs() < eps) & ((fans.distance - 10).abs() < eps)

    fans = fans[~(fzero_filter & fan_defaults)]
    blotches = blotches[~(bzero_filter & blotch_defaults)]
    rest = rest[~rest_zero_filter]

    # merge previously splitted together and return
    df = pd.concat([fans, blotches, rest])

    none = df[df.marking == 'none']
    rest = df[df.marking != 'none']

    # filter out markings outside tile frame
    # delta value is how much I allow x and y positions to be outside the
    # planet4 tile
    delta = 25
    q = "{} < x < {} and {} < y < {}".format(-delta, markings.img_x_size + delta,
                                             -delta, markings.img_y_size + delta)

    rest = rest.query(q)
    return pd.concat([rest, none])


def convert_times(df):
    logging.info("Starting time conversion now.")
    df.acquisition_date = pd.to_datetime(df.acquisition_date)
    df.created_at = pd.to_datetime(df.created_at,
                                   format='%Y-%m-%d %H:%M:%S %Z')
    logging.info("Time conversions done.")


def splitting_tutorials(rootpath, df):
    logging.info("Splitting off tutorials now.")
    tutorials = df[df.image_name == 'tutorial']
    tutfpath = '{}_tutorials.h5'.format(rootpath)
    tutorials = tutorials.drop(['image_id',
                                'image_url',
                                'image_name',
                                'local_mars_time'], axis=1)
    tutorials.to_hdf(tutfpath, 'df', format='t')

    logging.info("Tutorial split done.\nCreated %s.", tutfpath)
    return df[df.image_name != 'tutorial']


def produce_fast_read(rootpath, df):
    logging.info("Now writing fixed format datafile for "
                 "fast read-in of all data.")
    newfpath = '{0}_fast_all_read.h5'.format(rootpath)
    df.to_hdf(newfpath, 'df')
    logging.info("Created %s.", newfpath)


def convert_ellipse_angles(df):
    "Normalize angles from -180..180 to 0..180 (due to symmetry)."
    logging.info("Converting ellipse angles.")

    rowindex = (df.marking == 'blotch')
    df.loc[rowindex, 'angle'] = df.loc[rowindex, 'angle'] % 180
    logging.info("Conversion of ellipse angles done.")


def normalize_fan_angles(df):
    """Convert -180..180 angles to 0..360"""
    logging.info("Normalizing fan angles.")

    rowindex = (df.marking == 'fan')
    df.loc[rowindex, 'angle'] = df.loc[rowindex, 'angle'] % 360
    logging.info("Normalizing of fan angles done.")


def calculate_hirise_pixels(df):
    logging.info("Calculating and assigning hirise pixel coordinates")
    df = df.assign(hirise_x=lambda row: (row.x + 740 * (row.x_tile - 1)).round(),
                   hirise_y=lambda row: (row.y + 548 * (row.y_tile - 1)).round())
    logging.info("Hirise pixels coords added.")
    return df


def remove_duplicates_from_image_name_data(data):
    """remove duplicates from this data.

    Parameters
    ==========
    data: pd.DataFrame
        data filtered for one image_name

    Returns
    =======
    For each `user_name` and `image_id` found in `data` return only the data
    for the first found classification_id. There *should* only be one
    classification_id per user_name and image_id, but sometimes the queue
    presented the same image_id more than once to the same users. This removes
    any later in time classification_ids per user_name and image_id.
    """
    group = data.groupby(['image_id', 'user_name'], sort=False)

    def process_user_group(g):
        c_id = g[g.created_at == g.created_at.min()].classification_id.min()
        return g[g.classification_id == c_id]

    return group.apply(process_user_group).reset_index(drop=True)


def get_temp_fname(image_name):
    return str(data_root / ('temp_' + image_name + '.h5'))


def remove_duplicates(df):
    logging.info('Removing duplicates.')

    image_names = df.image_name.unique()

    def process_image_name(image_name):
        data = df[df.image_name == image_name]
        data = remove_duplicates_from_image_name_data(data)
        data.to_hdf(get_temp_fname(image_name), 'df')

    # parallel approach, u need to launch an ipcluster/controller for this work!
    c = Client()
    dview = c.direct_view()
    dview.push({'remove_duplicates_from_image_name_data':
                remove_duplicates_from_image_name_data,
                'data_root': data_root})
    lbview = c.load_balanced_view()
    lbview.map_sync(process_image_name, image_names)

    df = []
    for image_name in image_names:
        try:
            df.append(pd.read_hdf(get_temp_fname(image_name), 'df'))
        except OSError:
            continue
        else:
            os.remove(get_temp_fname(image_name))
    df = pd.concat(df, ignore_index=True)
    logging.info('Duplicates removal complete.')
    return df


def get_image_names(dbname):
    logging.info('Reading image_names from disk.')
    store = pd.HDFStore(dbname)
    image_names = store.select_column('df', 'image_name').unique()
    logging.info('Got image_names')
    return image_names


def merge_temp_files(dbname, image_names=None, do_odo=False):
    if do_odo:
        logging.info('Merging temp files with odo.')
    else:
        logging.info('Merging temp files manually.')

    if image_names is None:
        image_names = get_image_names(dbname)

    dbname = Path(dbname)
    newname = dbname.stem + '_cleaned' + dbname.suffix
    dbnamenew = dbname.with_name(newname)
    logging.info('Creating concatenated db file %s', dbnamenew)
    if not do_odo:
        df = []
    for image_name in image_names:
        try:
            if do_odo:
                odo('hdfstore://{}::df'.format(get_temp_fname(image_name)),
                    'hdfstore://{}::df'.format(dbnamenew))
            else:
                df.append(pd.read_hdf(get_temp_fname(image_name), 'df'))
        except OSError:
            continue
        else:
            os.remove(get_temp_fname(image_name))
    df = pd.concat(df, ignore_index=True)

    # change type to category
    to_category = ['image_name', 'image_id', 'image_url',
                   'user_name', 'marking', 'local_mars_time']
    for col in to_category:
        df[col] = df[col].astype('category')

    df.to_hdf(str(dbnamenew), 'df',
              format='table',
              data_columns=data_columns)
    logging.info('Duplicates removal complete.')
    return dbnamenew


def remove_duplicates_from_file(dbname, do_odo=False):
    logging.info('Removing duplicates.')

    image_names = get_image_names(dbname)

    def process_image_name(image_name):
        import pandas as pd
        data = pd.read_hdf(dbname, 'df', where='image_name==' + image_name)
        data = remove_duplicates_from_image_name_data(data)
        data.to_hdf(get_temp_fname(image_name), 'df')

    # parallel approach, u need to launch an ipcluster/controller for this work!
    c = Client()
    dview = c.direct_view()
    dview.push({'remove_duplicates_from_image_name_data':
                remove_duplicates_from_image_name_data,
                'data_root': data_root,
                'get_temp_fname': get_temp_fname})
    lbview = c.load_balanced_view()
    logging.info('Starting parallel processing.')
    lbview.map_sync(process_image_name, image_names)
    logging.info('Done clean up. Now concatenating results.')

    merge_temp_files(dbname, image_names, do_odo)


def create_season2_and_3_database():
    """Define season columns and write out seaon 2 and 3 database results.

    Has to be executed after the main reduction has finished.
    Installed as main command line script under name create_season2and3.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("db_fname",
                        help="path to database to be used.")
    args = parser.parse_args()
    logging.info('Starting production of season 2 and 3 database.')
    # read data for season2 and 3
    db = DBManager(args.db_fname)
    season23_image_names = db.season2and3_image_names
    where = "image_name in {}".format(list(season23_image_names))
    season23 = pd.read_hdf(db.dbname, 'df', where=where)

    fname_base = os.path.basename(db.dbname)
    root = os.path.dirname(db.dbname)
    fname_no_ext = os.path.splitext(fname_base)[0]
    rootpath = os.path.join(root, fname_no_ext)
    newfname = '{}_seasons2and3.h5'.format(rootpath)
    if os.path.exists(newfname):
        os.remove(newfname)
    season23.to_hdf(newfname, 'df', format='t', data_columns=data_columns)
    logging.info('Finished. Produced %s', newfname)


def read_csv_into_df(fname, chunks=1e6, test_n_rows=None):
    # creating reader object with pandas interface for csv parsing
    # doing this in chunks as its faster. Also, later will do a split
    # into multiple processes to do this.
    reader = pd.read_csv(fname, chunksize=chunks, na_values=['null'],
                         usecols=analysis_cols, nrows=test_n_rows,
                         engine='c')

    # read in data chunk by chunk and collect into python list
    data = [chunk for chunk in reader]
    logging.info("Data collected into list.")

    # convert list into Pandas dataframe
    df = pd.concat(data, ignore_index=True)
    logging.info("Conversion to dataframe complete.")
    data = 0
    return df


def main():
    import imp
    try:
        imp.find_module('tables')
    except ImportError:
        print("Please install the PyTables module. It is required.")
        sys.exit()
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_fname',
                        help="Provide the filename of the database "
                             "dump csv-file here.")
    parser.add_argument('--raw_times',
                        help="Do not parse the times into a Python datetime"
                             " object. For the stone-age. ;) Default:"
                             " parse into datetime object.",
                        action='store_true')
    parser.add_argument('--keep_dirt',
                        help="Do not filter for dirty data. Keep everything."
                             " Default: Do the filtering.",
                        action='store_true')
    parser.add_argument('--do_fastread',
                        help='Produce the fast-read database file for'
                             ' complete read into memory.',
                        action='store_true')
    parser.add_argument('--keep_dups',
                        help='Do not remove duplicates from database now '
                             ' (saves time).',
                        action='store_true')
    parser.add_argument('--test_n_rows',
                        help="Set this to do a test parse of n rows",
                        type=int, default=None)
    parser.add_argument('--only_dups',
                        help="Only do the duplicate removal",
                        action='store_true')

    args = parser.parse_args()

    t0 = time.time()
    logging.info("Starting reduction.")

    # creating file paths
    csvfpath = Path(args.csv_fname)
    fname = csvfpath.absolute()
    rootpath = fname.parent / fname.stem
    # path for database:
    newfpath = '{0}_queryable.h5'.format(rootpath)

    if args.only_dups is True:
        remove_duplicates_from_file(newfpath)
        return

    # as chunksize and nrows cannot be used together yet, i switch chunksize
    # to None if I want test_n_rows for a small test database:
    if args.test_n_rows is not None:
        chunks = None
    else:
        chunks = 1e6

    # first read CSV into dataframe
    df = read_csv_into_df(fname, chunks, args.test_n_rows)

    # convert times to datetime object
    if not args.raw_times:
        convert_times(df)

    # split off tutorials
    df = splitting_tutorials(rootpath, df)

    logging.info('Scanning for and dropping empty lines now.')
    df = df.dropna(how='all')
    logging.info("Dropped empty lines.")

    if not args.keep_dirt:
        logging.info("Now filtering for unwanted data.")
        df = filter_data(df)
        logging.info("Done removing incompletes.")

    convert_ellipse_angles(df)
    normalize_fan_angles(df)

    if args.do_fastread:
        produce_fast_read(rootpath, df)

    logging.info("Now writing query-able database file.")
    df.to_hdf(newfpath, 'df',
              format='table',
              data_columns=['image_name'])
    logging.info("Writing to HDF file finished. Created %s. "
                 "Reduction complete.", newfpath)

    # free memory
    df = 0

    if not args.keep_dups:
        remove_duplicates_from_file(newfpath)

    dt = time.time() - t0
    logging.info("Time taken: %f minutes.", dt / 60.0)

if __name__ == '__main__':
    main()
