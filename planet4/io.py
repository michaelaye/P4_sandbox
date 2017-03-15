import configparser
import datetime as dt
import logging
import os
import shutil
import sys
from pathlib import Path

import matplotlib.image as mplimg
import pandas as pd
import pkg_resources as pr

from . import stats
from .exceptions import NoFilesFoundError

try:
    from urllib import urlretrieve
except ImportError:
    from urllib.request import urlretrieve

pkg_name = __name__.split('.')[0]

configpath = Path.home() / ".{}.ini".format(pkg_name)

logger = logging.getLogger(__name__)


def get_config():
    """Read the configfile and return config dict.

    Returns
    -------
    dict
        Dictionary with the content of the configpath file.
    """
    if not configpath.exists():
        raise IOError("Config file {} not found.".format(str(configpath)))
    else:
        config = configparser.ConfigParser()
        config.read(str(configpath))
        return config


def set_database_path(dbfolder):
    """Use to write the database path into the config.

    Parameters
    ----------
    dbfolder : str or pathlib.Path
        Path to where planet4 will store clustering results by default.
    """
    try:
        d = get_config()
    except IOError:
        d = configparser.ConfigParser()
        d['planet4_db'] = {}
    d['planet4_db']['path'] = dbfolder
    with configpath.open('w') as f:
        d.write(f)
    print("Saved database path into {}.".format(configpath))


def get_data_root():
    d = get_config()
    data_root = Path(d['planet4_db']['path'])
    data_root.mkdir(exist_ok=True)
    return data_root


if not configpath.exists():
    print("No configuration file {} found.\n".format(configpath))
    savepath = input("Please provide the path where you want to store planet4 results:")
    set_database_path(savepath)
else:
    data_root = get_data_root()


location_target_codes = {'giza': ['0850'],
                         'spider_evolution': ['0950'],
                         'ithaca': ['0945', '0850', '0950']}


def dropbox():
    return Path.home() / 'Dropbox'


def p4data():
    return dropbox() / 'data' / 'planet4'


def analysis_folder():
    name = 'p4_analysis'
    if p4data().exists():
        path = p4data() / name
    else:
        path = dropbox() / name
    return path


def check_and_pad_id(imgid):
    imgid_template = "APF0000000"
    if len(imgid) < len(imgid_template):
        imgid = imgid_template[:-len(imgid)] + imgid
    return imgid


def get_subframe(url):
    """Download image if not there yet and return numpy array.

    Takes a data record (called 'line'), picks out the image_url.
    First checks if the name of that image is already stored in
    the image path. If not, it grabs it from the server.
    Then uses matplotlib.image to read the image into a numpy-array
    and finally returns it.
    """
    targetpath = data_root / 'images' / os.path.basename(url)
    targetpath.parent.mkdir(exist_ok=True)
    if not targetpath.exists():
        logger.info("Did not find image in cache. Downloading ...")
        sys.stdout.flush()
        path = urlretrieve(url)[0]
        logger.debug("Done.")
        shutil.move(path, str(targetpath))
    else:
        logger.debug("Found image in cache.")
    im = mplimg.imread(targetpath)
    return im


class P4DBName(object):

    def __init__(self, fname):
        self.p = Path(fname)
        date = str(self.name)[:10]
        self.date = dt.datetime(*[int(i) for i in date.split('-')])

    def __getattr__(self, name):
        "looking up things in the Path object if not in `self`."
        return getattr(self.p, name)


def get_latest_file(filenames):
    fnames = list(filenames)
    if len(fnames) == 0:
        raise NoFilesFoundError
    retval = P4DBName(fnames[0])
    dtnow = retval.date
    for fname in fnames[1:]:
        dt_to_check = P4DBName(fname).date
        if dt_to_check > dtnow:
            dtnow = dt_to_check
            retval = P4DBName(fname)
    return retval.p


def get_latest_cleaned_db(datadir=None):
    datadir = data_root if datadir is None else Path(datadir)
    h5files = datadir.glob('201*_queryable_cleaned*.h5')
    return get_latest_file(h5files)


def get_latest_season23_dbase(datadir=None):
    if datadir is None:
        datadir = data_root
    h5files = list(datadir.glob('201*_queryable_cleaned_seasons2and3.h5'))
    return get_latest_file(h5files)


def get_test_database():
    fname = pr.resource_filename('planet4', 'data/test_db.csv')
    return pd.read_csv(fname)


def get_latest_tutorial_data(datadir=None):
    if datadir is None:
        datadir = data_root

    tut_files = datadir.glob('/*_tutorials.h5')
    tut_files = [i for i in tut_files if i.parent[:4].isdigit()]
    if not tut_files:
        raise NoFilesFoundError
    return pd.read_hdf(str(get_latest_file(tut_files)), 'df')


def common_gold_ids():
    # read the common gold_ids to check
    with open('../data/gold_standard_commons.txt') as f:
        gold_ids = f.read()
    gold_ids = gold_ids.split('\n')
    del gold_ids[-1]  # last one is empty
    return gold_ids


def get_image_names_from_db(dbfname):
    """Return arrary of HiRISE image_names from database file.

    Parameters
    ----------
    dbfname : pathlib.Path or str
        Path to database file to be used.

    Returns
    -------
    numpy.ndarray
        Array of unique image names.
    """
    path = Path(dbfname)
    if path.suffix in ['.hdf', '.h5']:
        with pd.HDFStore(str(dbfname)) as store:
            return store.select_column('df', 'image_name').unique()
    elif path.suffix == '.csv':
        return pd.read_csv(dbfname).image_id.unique()


def get_latest_marked():
    return pd.read_hdf(str(get_latest_cleaned_db()), 'df',
                       where='marking!=None')


def get_image_id_from_fname(fname):
    "Return image_id from beginning of Path(fname).name"
    fname = Path(fname)
    name = fname.name
    return name.split('_')[0]


def get_image_ids_in_folder(folder, extension='.csv'):
    fnames = Path(folder).glob('*' + extension)
    return [get_image_id_from_fname(i) for i in fnames]


class PathManager(object):

    """Manage file paths and folders related to the analysis pipeline.

    Parameters
    ----------
    id_ : str
        The data item id that is used to determine sub-paths
    datapath : str or pathlib.Path, optional
        the base path from where to manage all derived paths. No default assumed
        to prevent errors.
    suffix : {'.hdf', '.h5', '.csv'}
        The suffix that controls the reader function to be used.

    Attributes
    ----------
    cut_dir : pathlib.Path
        Defined in `get_cut_folder`.
    """

    def __init__(self, id_, datapath='clustering', suffix='.csv', obsid='', cut=0.5,
                 extra_path=''):
        self._id = id_
        self.cut = cut
        self.obsid = obsid
        self.extra_path = extra_path

        if datapath is None:
            # take default path if none given
            self._datapath = Path(data_root)
        elif Path(datapath).is_absolute():
            # if given datapath is absolute, take only that:
            self._datapath = Path(datapath)
        else:
            # if it is relative, add it to data_root
            self._datapath = Path(data_root) / datapath
        self.suffix = suffix

        # point reader to correct function depending on required suffix
        if suffix in ['.hdf', '.h5']:
            self.reader = pd.read_hdf
        elif suffix == '.csv':
            self.reader = pd.read_csv

    @property
    def datapath(self):
        return self._datapath

    @property
    def id_(self):
        return check_and_pad_id(self._id)

    @property
    def path_so_far(self):
        p = self.datapath
        p /= self.extra_path
        p /= self.obsid
        return p

    @property
    def clustered_folder_name(self):
        "Subfolder name for the clustered data before fnotching."
        return 'just_clustering'

    @property
    def cut_folder_name(self):
        "subfolder name for the final catalog after applying `cut`."
        return 'applied_cut_{:.1f}'.format(self.cut)

    def get_path(self, marking, specific=''):
        p = self.path_so_far
        # now add the image_id
        p /= self.id_
        # add the specific sub folder
        p /= specific
        p /= (self.id_ + '_' + str(marking) + self.suffix)
        return p

    def get_df(self, fpath):
        try:
            return self.reader(str(fpath))
        except OSError:
            return None

    @property
    def fanfile(self):
        return self.get_path('fans')

    @property
    def fandf(self):
        return self.get_df(self.fanfile)

    @property
    def reduced_fanfile(self):
        return self.get_path('fans', self.clustered_folder_name)

    @property
    def reduced_fandf(self):
        return self.get_df(self.reduced_fanfile)

    @property
    def final_fanfile(self):
        return self.get_path('fans', self.cut_folder_name)

    @property
    def final_fandf(self):
        return self.get_df(self.final_fanfile)

    @property
    def blotchfile(self):
        return self.get_path('blotches')

    @property
    def blotchdf(self):
        return self.get_df(self.blotchfile)

    @property
    def reduced_blotchfile(self):
        return self.get_path('blotches', self.clustered_folder_name)

    @property
    def reduced_blotchdf(self):
        return self.get_df(self.reduced_blotchfile)

    @property
    def final_blotchfile(self):
        return self.get_path('blotches', self.cut_folder_name)

    @property
    def final_blotchdf(self):
        return self.get_df(self.final_blotchfile)

    @property
    def fnotchfile(self):
        return self.get_path('fnotches')

    @property
    def fnotchdf(self):
        return self.get_df(self.fnotchfile)


class DBManager(object):

    """Access class for database activities.

    Provides easy access to often used data items.

    Parameters
    ----------
    dbname : str, optional
        Path to database file to be used. Default: use get_latest_cleaned_db() to
        find it.

    Attributes
    ----------
    image_names
    image_ids
    n_image_ids
    n_image_names
    obsids : Alias to image_ids
    season2and3_image_names

    """

    def __init__(self, dbname=None):
        """Initialize DBManager class.

        Parameters
        ----------
        dbname : <str>
            Filename of database file to use. Default: Latest produced full
            database.
        """
        if dbname is None:
            self.dbname = str(get_latest_cleaned_db())
        else:
            self.dbname = str(dbname)

    def __repr__(self):
        s = "Database root: {}\n".format(Path(self.dbname).parent)
        s += "Database name: {}\n".format(Path(self.dbname).name)
        return s

    @property
    def orig_csv(self):
        p = Path(self.dbname)
        return p.parent / (p.name[:38] + '.csv')

    def set_latest_with_dupes_db(self, datadir=None):
        datadir = data_root if datadir is None else Path(datadir)
        h5files = datadir.glob('201*_queryable.h5')
        dbname = get_latest_file(h5files)
        print("Setting {} as dbname.".format(dbname.name))
        self.dbname = str(dbname)

    @property
    def image_names(self):
        """Return list of unique obsids used in database.

        See also
        --------
        get_image_names_from_db
        """
        return get_image_names_from_db(self.dbname)

    @property
    def image_ids(self):
        "Return list of unique image_ids in database."
        with pd.HDFStore(self.dbname) as store:
            return store.select_column('df', 'image_id').unique()

    @property
    def n_image_ids(self):
        return len(self.image_ids)

    @property
    def n_image_names(self):
        return len(self.image_names)

    @property
    def obsids(self):
        "Alias to self.image_ids."
        return self.image_ids

    def get_all(self, datadir=None):
        return pd.read_hdf(str(self.dbname), 'df')

    def get_obsid_markings(self, obsid):
        "Return marking data for given HiRISE obsid."
        return pd.read_hdf(self.dbname, 'df', where='image_name=' + obsid)

    def get_image_name_markings(self, image_name):
        "Alias for get_obsid_markings."
        return self.get_obsid_markings(image_name)

    def get_image_id_markings(self, image_id):
        "Return marking data for one Planet4 image_id"
        image_id = check_and_pad_id(image_id)
        return pd.read_hdf(self.dbname, 'df', where='image_id=' + image_id)

    def get_data_for_obsids(self, obsids):
        bucket = []
        for obsid in obsids:
            bucket.append(self.get_obsid_markings(obsid))
        return pd.concat(bucket, ignore_index=True)

    def get_classification_id_data(self, class_id):
        "Return data for one classification_id"
        return pd.read_hdf(self.dbname, 'df',
                           where="classification_id=='{}'".format(class_id))

    @property
    def season2and3_image_names(self):
        "numpy.array : List of image_names for season 2 and 3."
        image_names = self.image_names
        metadf = pd.DataFrame(pd.Series(image_names).astype('str'), columns=['image_name'])
        stats.define_season_column(metadf)
        return metadf[(metadf.season > 1) & (metadf.season < 4)].image_name.unique()

    def get_general_filter(self, f):
        return pd.read_hdf(self.dbname, 'df', where=f)


###
# general database helpers
###


def remove_tutorial(df):
    return df[df.image_name != 'tutorial']
