#!/usr/bin/env python
"""
Provide a TOKIO-aware HDF5 class that knows how to interpret schema versions
encoded in a TOKIO HDF5 file and translate a universal schema into file-specific
schemas.  Also supports dynamically mapping static HDF5 datasets into new
derived datasets dynamically.
"""

import datetime
import h5py
import pandas
from .. import config
from .. import timeseries
from _hdf5 import *

NO_SCHEMA_VERSION = -1

SCHEMA = {
    "1": {
        "datatargets/readbytes": "/datatargets/readbytes",
        "datatargets/writebytes": "/datatargets/writebytes",
        "datatargets/readrates": "/datatargets/readrates",
        "datatargets/writerates": "/datatargets/writerates",
        "mdtargets/open": "/mdtarget/open",
        "mdtargets/close": "/mdtarget/close",
        "mdtargets/mknod": "/mdtarget/mknod",
        "mdtargets/link": "/mdtarget/link",
        "mdtargets/unlink": "/mdtarget/unlink",
        "mdtargets/mkdir": "/mdtarget/mkdir",
        "mdtargets/rmdir": "/mdtarget/rmdir",
        "mdtargets/rename": "/mdtarget/rename",
        "mdtargets/getxattr": "/mdtarget/getxattr",
        "mdtargets/statfs": "/mdtarget/statfs",
        "mdtargets/setattr": "/mdtarget/setattr",
        "mdtargets/getattr": "/mdtarget/getattr",
        "mdservers/cpuuser": "/mdserver/cpuuser",
        "mdservers/cpusys": "/mdserver/cpusys",
        "mdservers/cpuidle": "/mdserver/cpuidle",
        "mdservers/memfree": "/mdserver/memfree",
        "mdservers/memused": "/mdserver/memused",
        "mdservers/memcached": "/mdserver/memcached",
        "mdservers/memslab": "/mdserver/memslab",
        "mdservers/memslab_unrecl": "/mdserver/memslab_unrecl",
        "mdservers/memtotal": "/mdserver/memtotal",
        "dataservers/cpuuser": "/dataserver/cpuuser",
        "dataservers/cpusys": "/dataserver/cpusys",
        "dataservers/cpuidle": "/dataserver/cpuidle",
        "dataservers/memfree": "/dataserver/memfree",
        "dataservers/memused": "/dataserver/memused",
        "dataservers/memcached": "/dataserver/memcached",
        "dataservers/memslab": "/dataserver/memslab",
        "dataservers/memslab_unrecl": "/dataserver/memslab_unrecl",
        "dataservers/memtotal": "/dataserver/memtotal",
        "fullness/bytes": "/fullness/bytes",
        "fullness/bytestotal": "/fullness/bytestotal",
        "fullness/inodes": "/fullness/inodes",
        "fullness/inodestotal": "/fullness/inodestotal",
        "failover/datatargets": "/failover/datatargets",
        "failover/mdtargets": "/failover/mdtargets",
    },
}

# Map keys which don't exist as datasets in the underlying HDF5 but can be
# calculated from datasets that _do_ exist to the functions that do these
# conversions
SCHEMA_DATASET_PROVIDERS = {
    "1": {
        "datatargets/readbytes": { 
            'func': convert_bytes_rates,
            'args': {
                'from_key': 'datatargets/readrates',
                'to_rates': False,
            },
        },
        "datatargets/writebytes": {
            'func': convert_bytes_rates,
            'args': {
                'from_key': 'datatargets/writerates',
                'to_rates': False,
            },
        },
        "datatargets/readrates": {
            'func': convert_bytes_rates,
            'args': {
                'from_key': 'datatargets/readbytes',
                'to_rates': True,
            },
        },
        "datatargets/writerates": {
            'func': convert_bytes_rates,
            'args': {
                'from_key': 'datatargets/writebytes',
                'to_rates': True,
            },
        },
    },
}

class Hdf5(h5py.File):
    """
    Create a parsed Hdf5 file class
    """
    def __init__(self, *args, **kwargs):
        """
        This is just an HDF5 file object; the magic is in the additional methods
        and indexing that are provided by the TOKIO Time Series-specific HDF5
        object.
        """
        super(Hdf5, self).__init__(*args, **kwargs)

        self.version = self.attrs.get('version', NO_SCHEMA_VERSION)

        # Connect the schema map to this object
        if self.version == NO_SCHEMA_VERSION:
            self.schema = {}
        elif self.version in SCHEMA:
            self.schema = SCHEMA[self.version]
        else:
            raise KeyError("Unknown schema version %s" % self.version)

        # Connect the schema dataset providers to this object
        if self.version in SCHEMA:
            self.dataset_providers = SCHEMA_DATASET_PROVIDERS[self.version]
        else:
            self.dataset_providers = {}

    def __getitem__(self, key):
        """
        Return the h5py.Dataset if key is a literal dataset name
                   h5py.Dataset if key maps directly to a literal dataset name
                                given the file schema version
                   numpy.ndarray if key maps to a provider function that can
                                 calculate the requested data
        """
        try:
            # If the dataset exists in the underlying HDF5 file, just return it
            return super(Hdf5, self).__getitem__(key)
        except KeyError:
            # If there is a straight mapping between the key and a dataset...
            key = key.lstrip('/') if isinstance(key, basestring) else key
            if key in self.schema:
                hdf5_key = self.schema[key]

                # If that mapped key exists in the underlying HDF5, use it
                if super(Hdf5, self).__contains__(hdf5_key):
                    return super(Hdf5, self).__getitem__(hdf5_key)

                # If that mapped key can be used to generate the requested dataset, generate it
                elif key in self.dataset_providers:
                    provider_func = self.dataset_providers[key]['func']
                    provider_args = self.dataset_providers[key]['args']
                    return provider_func(self, **provider_args)
            else:
                raise

    def get_index(self, target_datetime):
        """
        Turn a datetime object into an integer that can be used to reference
        specific times in datasets.

        """
        # Initialize our timestep if we don't already have this
        if self.timestep is None:
            if 'timestep' in self.attrs:
                self.timestep = self.attrs['timestep']
            elif 'FSStepsGroup/FSStepsDataSet' in self \
            and len(self['FSStepsGroup/FSStepsDataSet']) > 1:
                self.timestep = self['FSStepsGroup/FSStepsDataSet'][1] \
                    - self['FSStepsGroup/FSStepsDataSet'][0]
            else:
                self.timestep = config.LMT_TIMESTEP

        if 'first_timestamp' in self.attrs:
            time0 = datetime.datetime.fromtimestamp(self.attrs['first_timestamp'])
        else:
            time0 = datetime.datetime.fromtimestamp(self['FSStepsGroup/FSStepsDataSet'][0])

        return int((target_datetime - time0).total_seconds()) / int(self.timestep)

    def to_dataframe(self, dataset_name=None):
        """
        Convert the hdf5 class in a pandas dataframe
        """
        # Convenience:may put in lower case
        if dataset_name is None:
            dataset_name = '/FSStepsGroup/FSStepsDataSet'
        # Normalize to absolute path
        if not dataset_name.startswith('/'):
            dataset_name = '/' + dataset_name

        if dataset_name in ('/OSTReadGroup/OSTBulkReadDataSet',
                            '/OSTWriteGroup/OSTBulkWriteDataSet'):
            col_header_key = 'OSTNames'
        elif dataset_name == '/MDSOpsGroup/MDSOpsDataSet':
            col_header_key = 'OpNames'
        elif dataset_name == '/OSSCPUGroup/OSSCPUDataSet':
            col_header_key = 'OSSNames'
        else:
            col_header_key = None

        # Get column header from col_header_key
        if col_header_key is not None:
            col_header = self[dataset_name].attrs[col_header_key]
        elif dataset_name == '/FSMissingGroup/FSMissingDataSet' \
        and '/OSSCPUGroup/OSSCPUDataSet' in self:
            # Because FSMissingDataSet lacks the appropriate metadata in v1...
            col_header = self['/OSSCPUGroup/OSSCPUDataSet'].attrs['OSSNames']
        else:
            col_header = None

        # Retrieve timestamp indexes
        index = self['/FSStepsGroup/FSStepsDataSet'][:]

        # Retrieve hdf5 values
        if dataset_name == '/FSStepsGroup/FSStepsDataSet':
            values = None
        else:
            num_dims = len(self[dataset_name].shape)
            if num_dims == 1:
                values = self[dataset_name][:]
            elif num_dims == 2:
                values = self[dataset_name][:, :].T
            elif num_dims > 2:
                raise Exception("Can only convert 1d or 2d datasets to dataframe")

        return pandas.DataFrame(data=values,
                                index=[datetime.datetime.fromtimestamp(tstamp) for tstamp in index],
                                columns=col_header)

