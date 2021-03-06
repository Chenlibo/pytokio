#!/usr/bin/env python
"""
Process the Darshan daily summary json generated by summarize_darshanlogs.py and
generate a scoreboard of top sources of I/O based on user, file system, and
application
"""

import os
import re
import json
import gzip
import mimetypes
import collections
import argparse

def process_darshan_perfs(summary_jsons,
                          limit_fs=[], limit_user=[], limit_exe=[],
                          exclude_fs=[], exclude_user=[], exclude_exe=[]):
    """
    Ingest the per-log file system summary contained in the summary_json file(s)
    and produce a dictionary with bytes read/written reduced on application
    binary name, user name, and file system.
    """

    summary = {}
    for summary_json in summary_jsons:
        _, encoding = mimetypes.guess_type(summary_json)
        if encoding == 'gzip':
            summary.update(json.load(gzip.open(summary_json, 'r')))
        else:
            summary.update(json.load(open(summary_json, 'r')))

    regex_filename = re.compile(r'^([^_]+)_(.*?)_id(\d+)_.*.darshan')

    results = {
        'per_user': collections.defaultdict(lambda: collections.defaultdict(int)),
        'per_fs': collections.defaultdict(lambda: collections.defaultdict(int)),
        'per_exe': collections.defaultdict(lambda: collections.defaultdict(int)),
    }
    for darshan_log, counters in summary.iteritems():
        darshan_log_bn = os.path.basename(darshan_log)
        regex_match = regex_filename.search(darshan_log_bn)
        if regex_match:
            username = regex_match.group(1)
            exename = regex_match.group(2)
        elif '_' in darshan_log_bn:
            username = darshan_log_bn.split('_', 1)[0]
            exename = "<unknown>"
        else:
            username = "<unknown>"
            exename = "<unknown>"

        # apply limits, if applicable
        if (limit_user and username not in limit_user) \
        or (limit_exe and exename not in limit_exe) \
        or (exclude_user and username in exclude_user) \
        or (exclude_exe and exename in exclude_exe):
            continue

        for mount in counters.keys():
            if mount != '/':
                # if limit_fs in play, filter at the per-record basis
                if (limit_fs and mount not in limit_fs) \
                or (exclude_fs and mount in exclude_fs):
                    continue
                results['per_user'][username]['read_bytes'] += counters[mount].get('read_bytes', 0)
                results['per_user'][username]['write_bytes'] += counters[mount].get('write_bytes', 0)
                results['per_user'][username]['num_jobs'] += 1
                results['per_fs'][mount]['read_bytes'] += counters[mount].get('read_bytes', 0)
                results['per_fs'][mount]['write_bytes'] += counters[mount].get('write_bytes', 0)
                results['per_fs'][mount]['num_jobs'] += 1
                results['per_exe'][exename]['read_bytes'] += counters[mount].get('read_bytes', 0)
                results['per_exe'][exename]['write_bytes'] += counters[mount].get('write_bytes', 0)
                results['per_exe'][exename]['num_jobs'] += 1

    return results

def print_top(categorized_data, max_show=10):
    """
    Print the biggest I/O {users, exes, file systems}
    """
    names = {
        'per_fs': "File Systems",
        'per_exe': "Applications",
        'per_user': "Users",
    }

    categories = 0
    for category, rankings in categorized_data.iteritems():
        name = names.get(category, category)
        if categories > 0:
            print ""
        print "%2s  %40s %10s %10s %8s" % ('#', name, 'Read(GiB)', 'Write(GiB)', '# Jobs')
        print '=' * 75
        displayed = 0
        for winner in sorted(rankings, key=lambda x, r=rankings: r[x]['read_bytes'] + r[x]['write_bytes'], reverse=True):
            if len(winner) > 40:
#               winner_str = winner[0:19] + "..." + winner[-18:]
                winner_str = "..." + winner[-37:]
            else:
                winner_str = winner
            displayed += 1
            if displayed > max_show:
                break
            print "%2d. %40.40s %10.1f %10.1f %8d" % (displayed,
                                                      winner_str,
                                                      rankings[winner]['read_bytes'] / 2.0**30,
                                                      rankings[winner]['write_bytes'] / 2.0**30,
                                                      rankings[winner]['num_jobs'])
        categories += 1

def main(argv=None):
    """
    CLI wrapper around process_darshan_perfs()
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("summaryjson", type=str, nargs='+',
                        help="json output of darshan_per_fs_bytes.py")
    parser.add_argument("--json", action='store_true',
                        help="output in json format")
    parser.add_argument("--max-show", type=int, default=10,
                        help="show top N users, apps, file systems")
    group_fs = parser.add_mutually_exclusive_group()
    group_fs.add_argument("--limit-fs", type=str, default=None,
                          help="only process data targeting this file system")
    group_fs.add_argument("--exclude-fs", type=str, default=None,
                          help="exclude data targeting this file system")
    group_user = parser.add_mutually_exclusive_group()
    group_user.add_argument("--limit-user", type=str, default=None,
                            help="only process logs generated by this user")
    group_user.add_argument("--exclude-user", type=str, default=None,
                            help="exclude logs generated by this user")
    group_exe = parser.add_mutually_exclusive_group()
    group_exe.add_argument("--limit-exe", type=str, default=None,
                           help="only process logs generated by this binary")
    group_exe.add_argument("--exclude-exe", type=str, default=None,
                           help="exclude logs generated by this binary")

    args = parser.parse_args(argv)

    kwargs = {
        'limit_user': args.limit_user.split(',') if args.limit_user else [],
        'limit_fs': args.limit_fs.split(',') if args.limit_fs else [],
        'limit_exe': args.limit_exe.split(',') if args.limit_exe else [],
        'exclude_user': args.exclude_user.split(',') if args.exclude_user else [],
        'exclude_fs': args.exclude_fs.split(',') if args.exclude_fs else [],
        'exclude_exe': args.exclude_exe.split(',') if args.exclude_exe else [],
    }

    results = process_darshan_perfs(args.summaryjson, **kwargs)
    if args.json:
        print json.dumps(results, indent=4, sort_keys=True)
    else:
        print_top(results, max_show=args.max_show)

if __name__ == "__main__":
    main()
