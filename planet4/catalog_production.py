"""This module contains code for binding together the clustering and fnotching tools to create the
final catalog files.
When run as a script from command-line, it requires to launch a local ipcontroller for the parallel
processing.
If you execute this locally, you can create one with `ipcluster start -n <no>`, with <no> the number
of cores you want to provide to the parallel processing routines.
"""

import itertools
import logging
import string

import dask
import pandas as pd
from dask import delayed
from nbtools import execute_in_parallel
from planetarypy.pds.apps import get_index
from tqdm import tqdm

from . import io
from . import metadata as p4meta
from .projection import XY2LATLON, P4Mosaic, TileCalculator, create_RED45_mosaic

LOGGER = logging.getLogger(__name__)
# logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)


def fan_id_generator():
    for newid in itertools.product(string.digits + "abcdef", repeat=6):
        yield "F" + "".join(newid)


def blotch_id_generator():
    for newid in itertools.product(string.digits + "abcdef", repeat=6):
        yield "B" + "".join(newid)


def add_marking_ids(path, fan_id, blotch_id):
    """Add marking_ids for catalog to cluster results.

    Parameters
    ----------
    path : str, pathlib.Path
        Path to L1A image_id clustering result directory
    fan_id, blotch_id : generator
        Generator for marking_id
    """
    image_id = path.parent.name
    for kind, id_ in zip(["fans", "blotches"], [fan_id, blotch_id]):
        fname = str(path / f"{image_id}_L1A_{kind}.csv")
        try:
            df = pd.read_csv(fname)
        except FileNotFoundError:
            continue
        else:
            marking_ids = []
            for _ in range(df.shape[0]):
                marking_ids.append(next(id_))
            df["marking_id"] = marking_ids
            df.to_csv(fname, index=False)


def get_L1A_paths(obsid, savefolder):
    pm = io.PathManager(obsid=obsid, datapath=savefolder)
    paths = pm.get_obsid_paths("L1A")
    return paths


def cluster_obsid(obsid=None, savedir=None, imgid=None, dbname=None):
    """Cluster all image_ids for given obsid (=image_name).

    Parameters
    ----------
    obsid : str
        HiRISE obsid (= Planet four image_name)
    savedir : str or pathlib.Path
        Top directory path where the catalog will be stored. Will create folder if it
        does not exist yet.
    imgid : str, optional
        Convenience parameter: If `obsid` is not given and therefore is None, this `image_id` can
        be used to receive the respective `obsid` from the TileID class.
    """
    # import here to support parallel execution
    from planet4 import dbscan, markings

    # parameter checks
    if obsid is None and imgid is not None:
        obsid = markings.TileID(imgid).image_name
    elif obsid is None and imgid is None:
        raise ValueError("Provide either obsid or imgid.")

    # cluster
    dbscanner = dbscan.DBScanner(savedir=savedir, dbname=dbname)
    dbscanner.cluster_image_name(obsid)
    return obsid


def cluster_obsid_parallel(obsids, savedir, dbname):
    lazys = []
    for obsid in obsids:
        lazys.append(delayed(cluster_obsid)(obsid, savedir, dbname=dbname))
    return dask.compute(*lazys)


def fnotch_obsid(obsid=None, savedir=None, fnotch_via_obsid=False, imgid=None):
    """
    fnotch_via_obsid: bool, optional
        Switch to control if fnotching happens per image_id or per obsid
    """
    from planet4 import fnotching

    # fnotching / combining ambiguous cluster results
    # fnotch across all the HiRISE image
    # does not work yet correctly! Needs to scale for n_classifications
    if fnotch_via_obsid is True:
        fnotching.fnotch_obsid(obsid, savedir=savedir)
        fnotching.apply_cut_obsid(obsid, savedir=savedir)
    else:
        # default case: Fnotch for each image_id separately.
        fnotching.fnotch_image_ids(obsid, savedir=savedir)
        fnotching.apply_cut(obsid, savedir=savedir)
    return obsid


def fnotch_obsid_parallel(obsids, savedir):
    lazys = []
    for obsid in obsids:
        lazys.append(delayed(fnotch_obsid)(obsid, savedir))
    return dask.compute(*lazys)


class ReleaseManager:
    """Class to manage releases and find relevant files.

    Parameters
    ----------
    version : str
        Version string for this catalog. Same as datapath in other P4 code.
    obsids : iterable, optional
        Iterable of obsids that should be used for catalog file. Default is to use the full list of the default database, which is Seasons 2 and 3 at this point.
    overwrite : bool, optional
        Switch to control if already existing result folders for an obsid should be overwritten.
        Default: False
    """

    DROP_FOR_TILE_COORDS = [
        "xy_hirise",
        "SampleResolution",
        "LineResolution",
        "PositiveWest360Longitude",
        "Line",
        "Sample",
    ]

    FAN_COLUMNS_AS_PUBLISHED = [
        "marking_id",
        "angle",
        "distance",
        "tile_id",
        "image_x",
        "image_y",
        "n_votes",
        "obsid",
        "spread",
        "version",
        "vote_ratio",
        "x",
        "y",
        "x_angle",
        "y_angle",
        "l_s",
        "map_scale",
        "north_azimuth",
        "BodyFixedCoordinateX",
        "BodyFixedCoordinateY",
        "BodyFixedCoordinateZ",
        "PlanetocentricLatitude",
        "PlanetographicLatitude",
        "Longitude",
    ]
    BLOTCH_COLUMNS_AS_PUBLISHED = [
        "marking_id",
        "angle",
        "tile_id",
        "image_x",
        "image_y",
        "n_votes",
        "obsid",
        "radius_1",
        "radius_2",
        "vote_ratio",
        "x",
        "y",
        "x_angle",
        "y_angle",
        "l_s",
        "map_scale",
        "north_azimuth",
        "BodyFixedCoordinateX",
        "BodyFixedCoordinateY",
        "BodyFixedCoordinateZ",
        "PlanetocentricLatitude",
        "PlanetographicLatitude",
        "Longitude",
    ]

    def __init__(self, version, obsids=None, overwrite=False, dbname=None):
        self.catalog = f"P4_catalog_{version}"
        self.overwrite = overwrite
        self._obsids = obsids
        self.dbname = dbname

    @property
    def savefolder(self):
        "Path to catalog folder"
        return io.data_root / self.catalog

    @property
    def metadata_path(self):
        "Path to catalog metadata file."
        return self.savefolder / f"{self.catalog}_metadata.csv"

    @property
    def tile_coords_path(self):
        "Path to catalog tile coordinates file."
        return self.savefolder / f"{self.catalog}_tile_coords.csv"

    @property
    def tile_coords_path_final(self):
        "Path to final catalog tile coordinates file."
        return self.savefolder / f"{self.catalog}_tile_coords_final.csv"

    @property
    def obsids(self):
        """Return list of obsids for catalog production.

        If ._obsids is None, get default full obsids list for current default P4 database.
        """
        if self._obsids is None:
            db = io.DBManager(dbname=self.dbname)
            self._obsids = db.obsids
        return self._obsids

    @obsids.setter
    def obsids(self, values):
        self._obsids = values

    @property
    def fan_file(self):
        "Return path to fan catalog file."
        try:
            return next(self.savefolder.glob("*_fan.csv"))
        except StopIteration:
            print(f"No file found. Looking at {self.savefolder}.")

    @property
    def blotch_file(self):
        "Return path to blotch catalog file."
        try:
            return next(self.savefolder.glob("*_blotch.csv"))
        except StopIteration:
            print(f"No file found. Looking at {self.savefolder}.")

    @property
    def fan_merged(self):
        return self.fan_file.parent / f"{self.fan_file.stem}_meta_merged.csv"

    @property
    def blotch_merged(self):
        return self.blotch_file.parent / f"{self.blotch_file.stem}_meta_merged.csv"

    def read_fan_file(self):
        return pd.read_csv(self.fan_merged)

    def read_blotch_file(self):
        return pd.read_csv(self.blotch_merged)

    def check_for_todo(self, overwrite=None):
        if overwrite is None:
            overwrite = self.overwrite
        bucket = []
        for obsid in self.obsids:
            pm = io.PathManager(obsid=obsid, datapath=self.savefolder)
            path = pm.obsid_results_savefolder / obsid
            if path.exists() and overwrite is False:
                continue
            else:
                bucket.append(obsid)
        self.todo = bucket

    def get_parallel_args(self):
        return [(i, self.catalog, self.dbname) for i in self.todo]

    def get_no_of_tiles_per_obsid(self):
        all_data = pd.read_parquet(self.dbname)
        return all_data.groupby("image_name").image_id.nunique()

    @property
    def EDRINDEX_meta_path(self):
        return self.savefolder / f"{self.catalog}_EDRINDEX_metadata.csv"

    def calc_metadata(self):
        if not self.EDRINDEX_meta_path.exists():
            NAs = p4meta.get_north_azimuths_from_SPICE(self.obsids)
            edrindex = get_index("mro.hirise", "edr")
            p4_edr = (
                edrindex[edrindex.OBSERVATION_ID.isin(self.obsids)]
                .query('CCD_NAME=="RED4"')
                .drop_duplicates(subset="OBSERVATION_ID")
            )
            p4_edr = p4_edr.set_index("OBSERVATION_ID").join(
                NAs.set_index("OBSERVATION_ID")
            )
            p4_edr = p4_edr.join(self.get_no_of_tiles_per_obsid())
            p4_edr.rename(dict(image_id="# of tiles"), axis=1, inplace=True)
            p4_edr["map_scale"] = 0.25 * p4_edr.BINNING
            p4_edr.reset_index(inplace=True)
            p4_edr.to_csv(self.EDRINDEX_meta_path)
        else:
            p4_edr = pd.read_csv(self.EDRINDEX_meta_path)
        cols = [
            "OBSERVATION_ID",
            "IMAGE_CENTER_LATITUDE",
            "IMAGE_CENTER_LONGITUDE",
            "SOLAR_LONGITUDE",
            "START_TIME",
            "map_scale",
            "north_azimuth",
            "# of tiles",
        ]
        metadata = p4_edr[cols]
        metadata.to_csv(self.metadata_path, index=False, float_format="%.7f")
        LOGGER.info("Wrote %s", str(self.metadata_path))

    def calc_tile_coordinates(self):
        cubepaths = [P4Mosaic(obsid).mosaic_path for obsid in self.obsids]

        todo = []
        for cubepath in cubepaths:
            tc = TileCalculator(cubepath, read_data=False, dbname=self.dbname)
            if not tc.campt_results_path.exists():
                todo.append(cubepath)

        def get_tile_coords(cubepath):
            from planet4.projection import TileCalculator

            tilecalc = TileCalculator(cubepath, dbname=self.dbname)
            tilecalc.calc_tile_coords()

        if not len(todo) == 0:
            _ = execute_in_parallel(get_tile_coords, todo)

        bucket = []
        for cubepath in tqdm(cubepaths):
            tc = TileCalculator(cubepath, read_data=False, dbname=self.dbname)
            bucket.append(tc.tile_coords_df)
        coords = pd.concat(bucket, ignore_index=True, sort=False)
        coords.to_csv(self.tile_coords_path, index=False, float_format="%.7f")
        LOGGER.info("Wrote %s", str(self.tile_coords_path))

    @property
    def COLS_TO_MERGE(self):
        return [
            "obsid",
            "image_x",
            "image_y",
            "BodyFixedCoordinateX",
            "BodyFixedCoordinateY",
            "BodyFixedCoordinateZ",
            "PlanetocentricLatitude",
            "PlanetographicLatitude",
            "PositiveEast360Longitude",
        ]

    def merge_fnotch_results(self, fans, blotches):
        """Average multiple objects from fnotching into one.

        Because fnotching can compare the same object with more than one, it can appear more than once
        with different `vote_ratio` values in the results. We merge them here into one, simply
        averaging the vote_ratio. This increases the value of the `vote_ratio` number as it now
        has been created by several comparisons. It only occurs for 0.5 % of fans though.
        """
        out = []
        for df in [fans, blotches]:
            averaged = df.groupby("marking_id").mean()
            tmp = df.drop_duplicates(subset="marking_id").set_index("marking_id")
            averaged = averaged.join(tmp[["image_id", "obsid"]], how="inner")
            out.append(averaged.reset_index())

        return out

    def merge_all(self):
        # read in data files
        fans = pd.read_csv(self.fan_file)
        blotches = pd.read_csv(self.blotch_file)
        meta = pd.read_csv(self.metadata_path, dtype="str")
        tile_coords = pd.read_csv(self.tile_coords_path, dtype="str")

        # average multiple fnotch results
        fans, blotches = self.merge_fnotch_results(fans, blotches)

        # merge meta
        cols_to_merge = [
            "OBSERVATION_ID",
            "SOLAR_LONGITUDE",
            "north_azimuth",
            "map_scale",
        ]
        fans = fans.merge(
            meta[cols_to_merge], left_on="obsid", right_on="OBSERVATION_ID"
        )
        blotches = blotches.merge(
            meta[cols_to_merge], left_on="obsid", right_on="OBSERVATION_ID"
        )

        # drop unnecessary columns
        tile_coords.drop(
            self.DROP_FOR_TILE_COORDS, axis=1, inplace=True, errors="ignore"
        )
        # save cleaned tile_coords
        tile_coords.rename({"image_id": "tile_id"}, axis=1, inplace=True)
        tile_coords.to_csv(
            self.tile_coords_path_final, index=False, float_format="%.7f"
        )

        # merge campt results into catalog files
        fans, blotches = self.merge_campt_results(fans, blotches)

        # write out fans catalog
        fans.vote_ratio.fillna(1, inplace=True)
        fans.version = fans.version.astype("int")
        fans.rename(
            {
                "image_id": "tile_id",
                "SOLAR_LONGITUDE": "l_s",
                "PositiveEast360Longitude": "Longitude",
            },
            axis=1,
            inplace=True,
        )
        fans[self.FAN_COLUMNS_AS_PUBLISHED].to_csv(self.fan_merged, index=False)
        LOGGER.info("Wrote %s", str(self.fan_merged))

        # write out blotches catalog
        blotches.vote_ratio.fillna(1, inplace=True)
        blotches.rename(
            {
                "image_id": "tile_id",
                "SOLAR_LONGITUDE": "l_s",
                "PositiveEast360Longitude": "Longitude",
            },
            axis=1,
            inplace=True,
        )
        blotches[self.BLOTCH_COLUMNS_AS_PUBLISHED].to_csv(
            self.blotch_merged, index=False
        )
        LOGGER.info("Wrote %s", str(self.blotch_merged))

    def calc_marking_coordinates(self):
        fans = pd.read_csv(self.fan_file)
        blotches = pd.read_csv(self.blotch_file)
        combined = pd.concat([fans, blotches], sort=False)

        for obsid in tqdm(self.obsids):
            data = combined[combined.image_name == obsid]
            xy = XY2LATLON(data, self.savefolder, overwrite=self.overwrite)
            xy.process_inpath()

    def collect_marking_coordinates(self):
        bucket = []
        for obsid in self.obsids:
            xy = XY2LATLON(None, self.savefolder, obsid=obsid)
            bucket.append(pd.read_csv(xy.savepath).assign(obsid=obsid))

        ground = pd.concat(bucket, sort=False).drop_duplicates()
        ground.rename(dict(Sample="image_x", Line="image_y"), axis=1, inplace=True)
        return ground

    def fix_marking_coordinates_precision(self, df):
        fname = "tempfile.csv"
        df.to_csv(fname, float_format="%.7f")
        return pd.read_csv(fname, dtype="str")

    def merge_campt_results(self, fans, blotches):
        INDEX = ["obsid", "image_x", "image_y"]

        ground = self.collect_marking_coordinates().round(decimals=7)
        # ground = self.fix_marking_coordinates_precision(ground)
        fans = fans.merge(ground[self.COLS_TO_MERGE], on=INDEX)
        blotches = blotches.merge(ground[self.COLS_TO_MERGE], on=INDEX)
        return fans, blotches

    def perform_clustering(self):
        lazy_results = []

    def launch_catalog_production(self):
        # check for data that is unprocessed
        self.check_for_todo()

        # perform the clustering
        if len(self.todo) > 0:
            LOGGER.info("Performing the clustering.")
            results = cluster_obsid_parallel(self.todo, self.catalog, self.dbname)

            # create marking_ids
            fan_id = fan_id_generator()
            blotch_id = blotch_id_generator()
            for obsid in self.todo:
                paths = get_L1A_paths(obsid, self.catalog)
                for path in paths:
                    add_marking_ids(path, fan_id, blotch_id)

            # fnotch and apply cuts
            LOGGER.info("Start fnotching")
            results = fnotch_obsid_parallel(self.todo, self.catalog)

        # create summary CSV files of the clustering output
        LOGGER.info("Creating L1C fan and blotch database files.")
        create_roi_file(self.obsids, self.catalog, self.catalog)

        LOGGER.info("Creating the required RED45 mosaics for ground projections.")
        results = execute_in_parallel(create_RED45_mosaic, self.obsids)

        LOGGER.info("Calculating the center ground coordinates for all P4 tiles.")
        self.calc_tile_coordinates()

        LOGGER.info("Calculating ground coordinates for catalog.")
        self.calc_marking_coordinates()

        # calculate all metadata required for P4 analysis
        LOGGER.info("Writing summary metadata file.")
        self.calc_metadata()
        # merging metadata
        self.merge_all()


# @interactive
# def do_clustering(p4img, kind='fans'):
#     from planet4 import clustering
#     import pandas as pd

#     reduced = clustering.perform_dbscan(p4img, kind)
#     if reduced is None:
#         return None
#     series = [cluster.data for cluster in reduced]
#     n_members = [cluster.n_members for cluster in reduced]
#     n_rejected = [cluster.n_rejected for cluster in reduced]
#     df = pd.DataFrame(series)
#     df['image_id'] = p4img.imgid
#     df['n_members'] = n_members
#     df['n_rejected'] = n_rejected
#     return df


# @interactive
# def process_image_name(image_name):
#     from os.path import join as pjoin
#     import os
#     import pandas as pd
#     from planet4 import markings
#     HOME = os.environ['HOME']

#     dirname = pjoin(HOME, 'data/planet4/catalog_2_and_3')
#     if not os.path.exists(dirname):
#         os.makedirs(dirname)
#     blotchfname = pjoin(dirname, image_name + '_reduced_blotches.hdf')
#     fanfname = pjoin(dirname, image_name + '_reduced_fans.hdf')
#     if os.path.exists(blotchfname) and\
#             os.path.exists(fanfname):
#         return image_name + ' already done.'
#     db = io.DBManager()
#     data = db.get_image_name_markings(image_name)
#     img_ids = data.image_id.unique()
#     blotches = []
#     fans = []
#     for img_id in img_ids:
#         p4img = markings.TileID(img_id)
#         blotches.append(do_clustering(p4img, 'blotches'))
#         fans.append(do_clustering(p4img, 'fans'))
#     blotches = pd.concat(blotches, ignore_index=True)
#     blotches.to_hdf(blotchfname, 'df')
#     fans = pd.concat(fans, ignore_index=True)
#     fans.to_hdf(fanfname, 'df')
#     return image_name


def read_csvfiles_into_lists_of_frames(folders):
    bucket = dict(fan=[], blotch=[])
    for folder in folders:
        for markingfile in folder.glob("*.csv"):
            key = "fan" if markingfile.name.endswith("fans.csv") else "blotch"
            bucket[key].append(pd.read_csv(markingfile))
    return bucket


def create_roi_file(obsids, roi_name, datapath):
    """Create a Region of Interest file, based on list of obsids.

    For more structured analysis processes, we can create a summary file for a list of obsids
    belonging to a ROI.
    The alternative is to define to what ROI any final object belongs to and add that as a column
    in the final catalog.

    Parameters
    ----------
    obsids : iterable of str
        List of HiRISE obsids
    roi_name : str
        Name for ROI
    datapath : str or pathlib.Path
        Path to the top folder with the clustering output data.
    """
    Bucket = dict(fan=[], blotch=[])
    for obsid in tqdm(obsids):
        pm = io.PathManager(obsid=obsid, datapath=datapath)
        # get all L1C folders for current obsid:
        folders = pm.get_obsid_paths("L1C")
        bucket = read_csvfiles_into_lists_of_frames(folders)
        for key, val in bucket.items():
            try:
                df = pd.concat(val, ignore_index=True, sort=False)
            except ValueError:
                continue
            else:
                df["obsid"] = obsid
                Bucket[key].append(df)
    savedir = pm.path_so_far.parent
    if len(Bucket) == 0:
        func = LOGGER.warning
    else:
        func = LOGGER.info
    func("Found %i fans and %i blotches.", len(Bucket["fan"]), len(Bucket["blotch"]))
    for key, val in Bucket.items():
        try:
            df = pd.concat(val, ignore_index=True, sort=False)
        except ValueError:
            continue
        else:
            savename = f"{roi_name}_{pm.L1C_folder}_{key}.csv"
            savepath = savedir / savename
            for col in ["x_tile", "y_tile"]:
                df[col] = pd.to_numeric(df[col], downcast="signed")
            if "version" in df.columns:
                df["version"] = pd.to_numeric(df["version"], downcast="signed")
            df.to_csv(savepath, index=False, float_format="%.2f")
            print(f"Created {savepath}.")


# Main not functional

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument('db_fname',
#                         help="Provide the filename of the HDF database "
#                              "file here.")
#     args = parser.parse_args()

#     image_names = io.get_image_names_from_db(args.db_fname)
#     LOGGER.info('Found %i image_names', len(image_names))

#     c = Client()
#     dview = c.direct_view()
#     lbview = c.load_balanced_view()

#     dview.push({'do_clustering': do_clustering,
#                 'dbfile': args.db_fname})
#     results = lbview.map_async(process_image_name, image_names)
#     import time
#     import sys
#     import os
#     dirname = os.path.join(os.environ['HOME'], 'data/planet4/catalog_2_and_3')
#     while not results.ready():
#         print("{:.1f} %".format(100 * results.progress / len(image_names)))
#         sys.stdout.flush()
#         time.sleep(10)
#     for res in results.result:
#         print(res)
#     LOGGER.info('Catalog production done. Results in %s.', dirname)
