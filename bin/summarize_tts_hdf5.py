#!/usr/bin/env python
"""
Summarize the contents of a TOKIO TimeSeries (TTS) HDF5 file generated by
TimeSeries.commit_dataset().  This will eventually be merged with the
functionality provided by summarize_hdf5.py once the TTS HDF5 and pylmt formats
converge.
"""

import json
import datetime
import argparse
import tokio.timeseries
import tokio.connectors.hdf5

def humanize_units(byte_count, divisor=1024.0):
    """
    Convert a raw byte count into human-readable base2 units
    """
    units = ["bytes", "KiB", "MiB", "GiB", "TiB"]
    result = byte_count
    index = 0
    while index < len(units) - 1:
        new_result = result / divisor
        if new_result < 1.0:
            break
        else:
            index += 1
            result = new_result

    return result, units[index]

def summarize_tts_hdf5(hdf5_file):
    """
    Generate summary data based on the contents of TOKIO timeseries HDF5 file
    """
    read_bytes = hdf5_file['/datatargets/readbytes'][:, :].sum()
    write_bytes = hdf5_file['/datatargets/writebytes'][:, :].sum()

    # readrates and writerates come via the same collectd message, so if one is
    # missing, both are missing
    values = hdf5_file['/datatargets/readbytes'][:, :]
    num_missing = tokio.connectors.hdf5.missing_values(values).sum()
    total = values.shape[0] * values.shape[1]

    # find the row offset containing the first and last nonzero data
    first_time_idx = -1
    last_time_idx = -1
    nonzero_rows = tokio.connectors.hdf5.missing_values(values, inverse=True).sum(axis=1)
    for index, value in enumerate(nonzero_rows):
        if first_time_idx < 0 and value > 0:
            first_time_idx = index
        if value > 0:
            last_time_idx = index

    return {
        'read_bytes': read_bytes,
        'write_bytes': write_bytes,
        'missing_pts': num_missing,
        'total_pts': total,
        'missing_pct': (100.0 * float(num_missing) / total),
        'first_nonzero_idx': first_time_idx,
        'last_nonzero_idx': last_time_idx,
    }

def print_tts_hdf5_summary(results):
    """
    Format and print the summary data calculated by summarize_tts_hdf5()
    """
    print "Data Read:            %5.1f %s" % humanize_units(results['read_bytes'])
    print "Data Written:         %5.1f %s" % humanize_units(results['write_bytes'])
    print "Missing data points:  %9d" % results['missing_pts']
    print "Expected data points: %9d" % results['total_pts']
    print "Percent data missing: %8.1f%%" % results['missing_pct']
    print "First non-empty row:  %9d" % results['first_nonzero_idx']
    print "Last non-empty row:   %9d" % results['last_nonzero_idx']

def summarize_timesteps(hdf5_file):
    """
    Summarize read/write bytes for each time step using the raw HDF5 interface
    rather than casting into a DataFrame or TimeSeries
    """
    results = {}
    for dataset_name in '/datatargets/writebytes', '/datatargets/readbytes':
        timestamps = hdf5_file.get_timestamps(dataset_name)[:]
        sum_bytes = hdf5_file[dataset_name][:, :].sum(axis=1)
        for index, timestamp in enumerate(timestamps):
            output_key = 'read_bytes' if 'read' in dataset_name else 'write_bytes'
            output_val = sum_bytes[index]
            results[str(timestamp)] = {output_key: output_val}

    return results

def print_timestep_summary(summary):
    """
    Format and print the summary data calculated by summarize_timesteps()
    """
    for timestamp, values in summary.iteritems():
        print "%12s %14.2f read, %14.2f written" % (
            datetime.datetime.fromtimestamp(float(timestamp)),
            values.get('read_bytes', 0),
            values.get('write_bytes', 0))

def summarize_columns(hdf5_file):
    """
    Summarize read/write bytes for each column
    """
    results = {}
    for index, column_name in enumerate(list(hdf5_file.get_columns('/datatargets/readbytes'))):
        if column_name not in results:
            results[column_name] = {}
        results[column_name]['read_bytes'] = hdf5_file['/datatargets/readbytes'][:, index].sum()

    for index, column_name in enumerate(list(hdf5_file.get_columns('/datatargets/writebytes'))):
        if column_name not in results:
            results[column_name] = {}
        results[column_name]['write_bytes'] = hdf5_file['/datatargets/writebytes'][:, index].sum()

    return results

def print_column_summary(results):
    """
    Format and print the summary data calculated by summarize_columns()
    """
    for column_name, values in results.iteritems():
        print "%12s %14.2f read, %14.2f written" % (
            column_name,
            values.get('read_bytes', 0),
            values.get('write_bytes', 0))

def main(argv=None):
    """
    Summarize the contents of an HDF5 file generated by cache_collectdes_supplemental.py
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=str, help="HDF5 file to summarize")
    parser.add_argument('-j', '--json', action='store_true', help='output as json')
    parser.add_argument('--timesteps', action='store_true', help='print a summary at each timestep')
    parser.add_argument('--columns', action='store_true', help='print a summary of each column')
    args = parser.parse_args(argv)

    hdf5_file = tokio.connectors.hdf5.Hdf5(args.file, 'r')
    results = {
        'total': summarize_tts_hdf5(hdf5_file),
    }

    if args.timesteps:
        results['timesteps'] = summarize_timesteps(hdf5_file)
    if args.columns:
        results['columns'] = summarize_columns(hdf5_file)

    if args.json:
        print json.dumps(results, indent=4, sort_keys=True)
    else:
        print_tts_hdf5_summary(results['total'])
        if 'timesteps' in results:
            print_timestep_summary(results['timesteps'])
        if 'columns' in results:
            print_column_summary(results['columns'])

if __name__ == '__main__':
    main()
