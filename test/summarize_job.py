#!/usr/bin/env python
"""
Take a darshan log, extract the performance data from it, then use the
start/stop time from Darshan to extract the LMT data.  Relies on the
tokio.tool package, which uses H5LMT_HOME to determine where the LMT
HDF5 files are stored on the local system.
"""

import sys
import json
import argparse
import datetime
import warnings
import re

# import aggr_h5lmt
import tokio
import tokio.debug
import tokio.tools
import tokio.grabbers.darshan

### maps the "file_system" key from extract_darshan_perf to a h5lmt file name
FS_NAME_TO_H5LMT = {
    'scratch1': 'edison_snx11025.h5lmt',
    'scratch2': 'edison_snx11035.h5lmt',
    'scratch3': 'edison_snx11036.h5lmt',
    'cscratch': 'cori_snx11168.h5lmt',
}
FS_PATH = {
    '^/projects/radix-io': 'mira-fs1',
    '^/scratch1' : 'scratch1',
    '^/scratch2' : 'scratch2',
    '^/scratch3' : 'scratch3',
    '^/global/cscratch1' : 'cscratch',
    '^/var/opt/cray/dws/mounts/.*/ss/' : 'bb-shared',
    '^/var/opt/cray/dws/mounts/.*/ps/' : 'bb-private',
}
FS_NAME_TO_HOST = {
    'mira-fs1':   "mira",
    'scratch1':   "edison",
    'scratch2':   "edison",
    'scratch3':   "edison",
    'cscratch':   "cori",
    'bb-shared':  "cori",
    'bb-private': "cori",
}

### Empirically, it looks like we have to add one more LMT timestep after the
### Darshan log registers completion to capture the full amount of data
### generated by the darshan job.  This might be due to write-back cache on the
### client, or ??? for reads.  Or just clock drift between the compute nodes and
### the Sonexion nodes.
LMT_EXTEND_WINDOW = datetime.timedelta(seconds=tokio.LMT_TIMESTEP * 1)

def extract_lmt_from_darshan_perf(darshan_perf_data):
    """
    Given a dict containing the data from extract_darshan_perf, generate a dict
    containing a summary of the LMT data that corresponds to the start_time and
    stop_time from the Darshan perf data.
    """
    pass


def _identify_fs_from_path(path, mounts):
    """
    Scan a list of mount points and try to identify the one that matches the
    given path
    """
    max_match = 0
    matching_mount = None
    for mount in mounts:
        if path.startswith(mount) and len(mount) > max_match:
            max_match = len(mount)
            matching_mount = mount
    return matching_mount

def summarize_darshan(darshan_base_data=None,
                      darshan_perf_data=None,
                      darshan_total_data=None):
    """
    Take the results of extract_darshan_* and return only the counters of
    interest.
    """
    ### we assume that mpiio activity is a superset of posix, but posix activity
    ### is never a superset of mpiio
    API_CHECK_ORDER = [ 'mpiio', 'posix' ]

    results = {}
    ### extract all the counters from the file's common header
    if darshan_base_data is not None:
        darshan_header_src = darshan_base_data
    elif darshan_perf_data is not None:
        darshan_header_src = darshan_perf_data
    elif darshan_total_data is not None:
        darshan_header_src = darshan_total_data
    else:
        raise Exception("no viable source of darshan header info provided")

    if 'header' in darshan_header_src:
        results['walltime'] = darshan_header_src['header'].get('walltime')
        results['end_time'] = darshan_header_src['header'].get('end_time')
        results['start_time'] = darshan_header_src['header'].get('start_time')
        results['jobid'] = darshan_header_src['header'].get('jobid')
        results['app'] = darshan_header_src['header'].get('exe')
        if results['app'] is not None:
            results['app'] = results['app'][0]

    ### hopefully the Darshan log isn't just empty...
    if darshan_perf_data is not None and 'counters' in darshan_perf_data:
        ### extract POSIX performance counters if present
        if 'posix' in darshan_perf_data['counters'] \
        and '_perf' in darshan_perf_data['counters']['posix']:
            results['total_gibs_posix'] = darshan_perf_data['counters']['posix']['_perf'].get('total_bytes')
            if results['total_gibs_posix'] is not None:
                results['total_gibs_posix'] /= 2.0**30
            results['agg_perf_by_slowest_posix'] = darshan_perf_data['counters']['posix']['_perf'].get('agg_perf_by_slowest')
            results['io_time'] = darshan_perf_data['counters']['posix']['_perf'].get('slowest_rank_io_time_unique_files')
            if results['io_time'] is not None:
                results['io_time'] += darshan_perf_data['counters']['posix']['_perf'].get('time_by_slowest_shared_files')

    if darshan_base_data is not None and 'counters' in darshan_base_data:
        ### try to find the most-used API, the most time spent in that api
        biggest_api = {}
        for api_name in darshan_base_data['counters'].keys():
            biggest_api[api_name] = {
                'write': 0,
                'read': 0,
            }
            for file_path in darshan_base_data['counters'][api_name]:
                for rank, record in darshan_base_data['counters'][api_name][file_path].iteritems():
                    bytes_read = record.get('BYTES_READ')
                    if bytes_read is not None:
                        biggest_api[api_name]['read'] += bytes_read
                    bytes_written = record.get('BYTES_WRITTEN')
                    if bytes_written is not None:
                        biggest_api[api_name]['write'] += bytes_written

        results['biggest_write_api'] = max(biggest_api, key=lambda k: biggest_api[k]['write'])
        results['biggest_read_api'] = max(biggest_api, key=lambda k: biggest_api[k]['read'])
        results['biggest_write_api_bytes'] = biggest_api[results['biggest_write_api']]['write']
        results['biggest_read_api_bytes'] = biggest_api[results['biggest_read_api']]['read']

        ### try to find the most-used file system based on the most-used API
        biggest_fs = {}
        mounts = darshan_base_data['mounts'].keys()
        for api_name in results['biggest_read_api'], results['biggest_write_api']:
            for file_path in darshan_base_data['counters'][api_name]:
                for rank, record in darshan_base_data['counters'][api_name][file_path].iteritems():
                    key = _identify_fs_from_path(file_path, mounts)
                    if key is None:
                        key = '_unknown' ### for stuff like STDIO
                    if key not in biggest_fs:
                        biggest_fs[key] = { 'write': 0, 'read': 0 }
                    bytes_read = record.get('BYTES_READ')
                    if bytes_read is not None:
                        biggest_fs[key]['read'] += bytes_read
                    bytes_written = record.get('BYTES_WRITTEN')
                    if bytes_written is not None:
                        biggest_fs[key]['write'] += bytes_written
        results['biggest_write_fs'] = max(biggest_fs, key=lambda k: biggest_fs[k]['write'])
        results['biggest_read_fs'] = max(biggest_fs, key=lambda k: biggest_fs[k]['read'])
        results['biggest_write_fs_bytes'] = biggest_fs[results['biggest_write_fs']]['write']
        results['biggest_read_fs_bytes'] = biggest_fs[results['biggest_read_fs']]['read']

    return results

### the following have to be synthesized
#   'lmt_bytes_covered'
#   'file_system'

def summarize_byterate_df(df, rw, timestep=None):
    """
    Calculate some interesting statistics from a dataframe containing byte rate
    data.
    """
    assert rw in [ 'read', 'written' ]
    if timestep is None:
        if df.shape[0] < 2:
            raise Exception("must specify timestep for single-row dataframe")
        timestep = (df.index[1].to_pydatetime() - df.index[0].to_pydatetime()).total_seconds()
    results = {}
    results['tot_bytes_%s' % rw] = df.sum().sum() * timestep
    results['tot_gibs_%s' % rw] = results['tot_bytes_%s' % rw] / 2.0**30
    results['ave_bytes_%s_per_timestep' % rw] = (df.sum(axis=1) / df.columns.shape[0]).mean() * timestep
    results['ave_gibs_%s_per_timestep' % rw] = results['ave_bytes_%s_per_timestep' % rw] / 2.0**30
    results['frac_zero_%s' % rw] = float((df == 0.0).sum().sum()) / float((df.shape[0]*df.shape[1]))
    return results

def summarize_cpu_df(df, servertype):
    """
    Calculate some interesting statistics from a dataframe containing CPU load
    data.
    """
    assert servertype in [ 'oss', 'mds' ]
    results = {}
    results['ave_%s_cpu' % servertype] = df.mean().mean()
    results['max_%s_cpu' % servertype] = df.max().max()
    return results

def summarize_missing_df(df):
    """
    frac_missing
    """
    results = {
        'frac_missing': float((df != 0.0).sum().sum()) / float((df.shape[0]*df.shape[1]))
    }
    return results

def merge_dicts(dict1, dict2, assertion=True, prefix=None):
    """
    Take two dictionaries and merge their keys.  Optionally raise an exception
    if a duplicate key is found, and optionally merge the new dict into the old
    after adding a prefix to every key.
    """
    for key, value in dict2.iteritems():
        if prefix is not None:
            new_key = prefix + key
        else:
            new_key = key
        if assertion:
            assert new_key not in dict1
        dict1[new_key] = value

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--diameter", help="include diameter (Cray XC only); requires access to sacct", action="store_true")
    parser.add_argument("-o", "--ost", help="add information about OST fullness/failover", action="store_true")
    parser.add_argument("-j", "--json", help="output in json", action="store_true")
    parser.add_argument("-c", "--concurrentjobs", help="add number of jobs concurrently running from jobsdb", action="store_true")
    parser.add_argument("-f", "--filesystem", type=str, default=None, help="file system name (e.g., cscratch, bb-private)")
    parser.add_argument("files", nargs='*', help="darshan logs to process")
    args = parser.parse_args()

    sorted_keys = None
    csv_rows = []
    json_rows = []
    for darshan_log_file in args.files:
        results = {}

        ### extract the performance data from the darshan log
        darshan_perf_data = tokio.grabbers.darshan.darshan_parser_perf(darshan_log_file)
        darshan_base_data = tokio.grabbers.darshan.darshan_parser_base(darshan_log_file)

        ### define start/end time from darshan log.  TODO: make this optional
        datetime_start = datetime.datetime.fromtimestamp(int(darshan_perf_data['header']['start_time']))
        datetime_end = datetime.datetime.fromtimestamp(int(darshan_perf_data['header']['end_time']))
        print "%s - %s" % (datetime_start, datetime_end)

        ### figure out the H5LMT file corresponding to this run
        h5lmt_file = FS_NAME_TO_H5LMT.get(args.filesystem)
        if h5lmt_file is None:
            raise Exception("Unknown file system %s" % args.filesystem)

        ### read rates
        module_results = summarize_byterate_df(
            tokio.tools.get_dataframe_from_time_range(h5lmt_file,
                                                      '/OSTReadGroup/OSTBulkReadDataSet',
                                                      datetime_start,
                                                      datetime_end),
            'read'
        )
        merge_dicts(results, module_results, prefix='lmt_')

        ### write rates
        module_results = summarize_byterate_df(
            tokio.tools.get_dataframe_from_time_range(h5lmt_file,
                                                      '/OSTWriteGroup/OSTBulkWriteDataSet',
                                                      datetime_start,
                                                      datetime_end),
            'written'
        )
        merge_dicts(results, module_results, prefix='lmt_')
 
        ### oss cpu loads
        module_results = summarize_cpu_df(
            tokio.tools.get_dataframe_from_time_range(h5lmt_file,
                                                      '/OSSCPUGroup/OSSCPUDataSet',
                                                      datetime_start,
                                                      datetime_end),
            'oss'
        )
        merge_dicts(results, module_results, prefix='lmt_')

        ### mds cpu loads
        module_results = summarize_cpu_df(
            tokio.tools.get_dataframe_from_time_range(h5lmt_file,
                                                      '/MDSCPUGroup/MDSCPUDataSet',
                                                      datetime_start,
                                                      datetime_end),
            'mds'
        )
        merge_dicts(results, module_results, prefix='lmt_')
 
        ### missing data
        module_results = summarize_missing_df(
            tokio.tools.get_dataframe_from_time_range(h5lmt_file,
                                                      '/FSMissingGroup/FSMissingDataSet',
                                                      datetime_start,
                                                      datetime_end))
        merge_dicts(results, module_results, prefix='lmt_')

        ### get the summary of the Darshan log
        module_results = summarize_darshan(darshan_perf_data=darshan_perf_data,
            darshan_base_data=darshan_base_data)
        merge_dicts(results, module_results, prefix='darshan_')

        ### get the diameter of the job (Cray XC)
        if args.diameter:
            module_results = job_diameter.get_job_diameter(darshan_perf_data['jobid'])
            merge_dicts(results, module_results, prefix='crayxc_')

        ### get the concurrently running jobs (NERSC)
        if args.concurrentjobs:
            ### TODO: fix the API for this
            results['job_concurrent_jobs'] = nersc_jobsdb.get_concurrent_jobs(darshan_log_file)

        ### get Lustre server status (Sonexion)
        if args.ost:
            ### Divine the sonexion name from the file system map
            snx_name = FS_NAME_TO_H5LMT[darshan_perf_data['file_system']].split('_')[-1].split('.')[0]

            ### get the OST fullness summary
            module_results = ost_fullness.get_fullness_at_datetime(snx_name,
                datetime.datetime.fromtimestamp(long(darshan_perf_data['start_time'])))
            merge_dicts(results, module_results, prefix='fshealth_')

            ### get the OST failure status
            module_results = ost_fullness.get_failures_at_datetime(snx_name,
                datetime.datetime.fromtimestamp(long(darshan_perf_data['start_time'])))

            # Note that get_failures_at_datetime will clobber the
            # ost_timestamp_* keys from get_fullness_at_datetime above;
            # these aren't used for correlation analysis and should be
            # pretty close anyway.
            merge_dicts(results, module_data, False, prefix='fshealth_')

            # a measure, in sec, expressing how far before the job our OST fullness data was measured
            results['ost_fullness_lead_secs'] = results['darshan_start_time'] - results['ost_target_timestamp']

            ### ost_bad_pct becomes the percent of OSTs in file system which are
            ### in an abnormal state
            results["ost_bad_pct"] = 100.0 * float(results["ost_bad_ost_count"]) / float(results["ost_count"])
            
            # a measure, in sec, expressing how far before the job our OST failure data was measured
            results['ost_failures_lead_secs'] = results['darshan_start_time'] - results['ost_target_timestamp']

            ### drop some keys, used for debugging, that are clobbered by
            ### combining get_failures_at_datetime and get_fullness_at_datetime
            ### anyway
            for key in "ost_next_timestamp", "ost_requested_timestamp", "ost_target_timestamp":
                results.pop(key)

        if sorted_keys is None:
            sorted_keys = sorted(results.keys())
            csv_rows = [sorted_keys]

        sorted_values = []
        for key in sorted_keys:
            sorted_values.append(results[key])

        csv_rows.append(sorted_values)
        json_rows.append(results)

    if args.json:
        print json.dumps(json_rows, indent=4, sort_keys=True)
    else:
        for csv_row in csv_rows:
            print ','.join(str(x) for x in csv_row)
