import json
import math

from collections import Counter, OrderedDict
from warmup.html import DIFF_LEGEND, get_symbol, html_histogram, HTML_TABLE_TEMPLATE
from warmup.html import HTML_DIFF_TABLE_TEMPLATE, HTML_PAGE_TEMPLATE, HTML_SYMBOLS
from warmup.latex import end_document, end_longtable, end_table, escape, format_median_ci
from warmup.latex import format_median_error, get_latex_symbol_map, preamble
from warmup.latex import start_longtable, start_table, STYLE_SYMBOLS
from warmup.statistics import (bootstrap_runner, median_iqr,
                               get_absolute_delta_using_fastest_seg)

JSON_VERSION_NUMBER = '2'

TITLE = 'Summary of benchmark classifications'
TABLE_FORMAT = 'll@{\hspace{0cm}}ll@{\hspace{-1cm}}r@{\hspace{0cm}}r@{\hspace{0cm}}r@{\hspace{0cm}}l@{\hspace{.3cm}}ll@{\hspace{-1cm}}r@{\hspace{0cm}}r@{\hspace{0cm}}r'
TABLE_HEADINGS_START1 = '\\multicolumn{1}{c}{\\multirow{2}{*}{}}&'
TABLE_HEADINGS_START2 = '&'
TABLE_HEADINGS1 = '&&\\multicolumn{1}{c}{} &\\multicolumn{1}{c}{Steady}&\\multicolumn{1}{c}{Steady}&\\multicolumn{1}{c}{Steady}'
TABLE_HEADINGS2 = '&&\\multicolumn{1}{c}{Class.} &\\multicolumn{1}{c}{iter (\#)} &\\multicolumn{1}{c}{iter (s)}&\\multicolumn{1}{c}{perf (s)}'

BLANK_CELL = '\\begin{minipage}[c][\\blankheight]{0pt}\\end{minipage}'

# List indices (used in diff summaries).
CLASSIFICATIONS = 0  # Indices for top-level summary lists.
STEADY_ITER = 1
STEADY_ITER_VAR = 2
STEADY_STATE_TIME = 3
STEADY_STATE_TIME_VAR = 4
INTERSECTION = 5
SAME = 0  # Indices for nested lists.
DIFFERENT = 1
BETTER = 2
WORSE = 3
SKIPPED_BEFORE = 0
SKIPPED_AFTER = 1


def collect_summary_statistics(data_dictionaries, delta, steady_state, quality='LOW'):
    """Create summary statistics of a dataset with classifications.
    Note that this function returns a dict which is consumed by other code to
    create tables. It also DEFINES the JSON format which the ../bin/warmup_stats
    script dumps to file.
    """

    assert type(delta) in [str, unicode]

    summary_data = dict()
    # Although the caller can pass >1 json file, there should never be two
    # different machines.
    assert len(data_dictionaries) == 1
    machine = data_dictionaries.keys()[0]
    summary_data = { 'machines': { machine: dict() }, 'warmup_format_version': JSON_VERSION_NUMBER }
    # Parse data dictionaries.
    keys = sorted(data_dictionaries[machine]['wallclock_times'].keys())
    for key in sorted(keys):
        wallclock_times = data_dictionaries[machine]['wallclock_times'][key]
        if len(wallclock_times) == 0:
            print('WARNING: Skipping: %s from %s (no executions)' %
                   (key, machine))
        #elif data_dictionaries[machine]['classifications'][key][0] == "errored":
        #    # XXX assumes all pexecs crashed if the first did.
        #    assert len(wallclock_times[0]) == 0
        #    print('WARNING: Skipping: %s from %s (benchmark errored (crashed))' %
        #          (key, machine))
        else:
            bench, vm, variant = key.split(':')
            if vm not in summary_data['machines'][machine].keys():
                summary_data['machines'][machine][vm] = dict()
            # Get information for all p_execs of this key.
            categories = list()
            steady_state_means = list()
            steady_iters = list()
            time_to_steadys = list()
            n_pexecs = len(data_dictionaries[machine]['wallclock_times'][key])
            segments_for_bootstrap_all_pexecs = list()  # Steady state segments for all pexecs.
            # Lists of changepoints, outliers and segment means for each process execution.
            changepoints, outliers, segments = list(), list(), list()
            for p_exec in xrange(n_pexecs):
                segments_for_bootstrap_this_pexec = list()  # Steady state segments for this pexec.
                changepoints.append(data_dictionaries[machine]['changepoints'][key][p_exec])
                segments.append(data_dictionaries[machine]['changepoint_means'][key][p_exec])
                outliers.append(data_dictionaries[machine]['all_outliers'][key][p_exec])
                categories.append(data_dictionaries[machine]['classifications'][key][p_exec])
                # Next we calculate the iteration at which a steady state was
                # reached, it's average segment mean and the time to reach a
                # steady state. However, the last segment may be equivalent to
                # its adjacent segments, so we first need to know which segments
                # are steady-state segments.
                if data_dictionaries[machine]['classifications'][key][p_exec] in ('no steady state', "timeout", "errored"):
                    continue
                # Capture the last steady state segment for bootstrapping.
                segment_data = list()
                if data_dictionaries[machine]['changepoints'][key][p_exec]:
                    start = data_dictionaries[machine]['changepoints'][key][p_exec][-1]
                else:
                    start = 0  # No changepoints in this pexec.
                end = len(data_dictionaries[machine]['wallclock_times'][key][p_exec])
                for segment_index in xrange(start, end):
                    if segment_index in data_dictionaries[machine]['all_outliers'][key][p_exec]:
                        continue
                    segment_data.append(data_dictionaries[machine]['wallclock_times'][key][p_exec][segment_index])
                segments_for_bootstrap_this_pexec.append(segment_data)

                abs_delta = get_absolute_delta_using_fastest_seg(
                    delta, data_dictionaries[machine]['changepoint_means'][key][p_exec])

                first_steady_segment = len(data_dictionaries[machine]['changepoint_means'][key][p_exec]) - 1
                num_steady_segments = 1
                last_segment_mean = data_dictionaries[machine]['changepoint_means'][key][p_exec][-1]
                last_segment_var = data_dictionaries[machine]['changepoint_vars'][key][p_exec][-1]
                lower_bound = min(last_segment_mean - last_segment_var, last_segment_mean - abs_delta)
                upper_bound = max(last_segment_mean + last_segment_var, last_segment_mean + abs_delta)
                # This for loop deals with segments that are equivalent to the
                # final, steady state segment.
                for index in xrange(len(data_dictionaries[machine]['changepoint_means'][key][p_exec]) - 2, -1, -1):
                    current_segment_mean = data_dictionaries[machine]['changepoint_means'][key][p_exec][index]
                    current_segment_var = data_dictionaries[machine]['changepoint_vars'][key][p_exec][index]
                    if (current_segment_mean + current_segment_var >= lower_bound and
                            current_segment_mean - current_segment_var<= upper_bound):
                        # Extract this segment from the wallclock data for bootstrapping.
                        segment_data = list()
                        if index == 0:
                            start = 0
                            end = data_dictionaries[machine]['changepoints'][key][p_exec][index] + 1
                        else:
                            start = data_dictionaries[machine]['changepoints'][key][p_exec][index - 1] + 1
                            end = data_dictionaries[machine]['changepoints'][key][p_exec][index] + 1
                        for segment_index in xrange(start, end):
                            if segment_index in data_dictionaries[machine]['all_outliers'][key][p_exec]:
                                continue
                            segment_data.append(data_dictionaries[machine]['wallclock_times'][key][p_exec][segment_index])
                        segments_for_bootstrap_this_pexec.append(segment_data)
                        # Increment / decrement counters.
                        first_steady_segment -= 1
                        num_steady_segments += 1
                    else:
                        break
                segments_for_bootstrap_all_pexecs.append(segments_for_bootstrap_this_pexec)
                # End of code to capture segments for bootstrapping.
                steady_state_mean = (math.fsum(data_dictionaries[machine]['changepoint_means'][key][p_exec][first_steady_segment:])
                                     / float(num_steady_segments))
                steady_state_means.append(steady_state_mean)
                # Not all process execs have changepoints. However, all
                # p_execs will have one or more segment mean.
                if data_dictionaries[machine]['classifications'][key][p_exec] != 'flat':
                    steady_iter = data_dictionaries[machine]['changepoints'][key][p_exec][first_steady_segment - 1]
                    steady_iters.append(steady_iter + 1)
                    to_steady = 0.0
                    for index in xrange(steady_iter):
                        to_steady += data_dictionaries[machine]['wallclock_times'][key][p_exec][index]
                    time_to_steadys.append(to_steady)
                else:  # Flat execution, with no changepoints.
                    steady_iters.append(1)
                    time_to_steadys.append(0.0)
            # Get overall and detailed categories.
            categories_set = set(categories)
            if len(categories_set) == 1:  # NB some benchmarks may have errored.
                reported_category = categories[0]
            elif categories_set == set(['flat', 'warmup']):
                reported_category = 'good inconsistent'
            else:  # Bad inconsistent.
                reported_category = 'bad inconsistent'
            cat_counts = dict()
            for category, occurences in Counter(categories).most_common():
                cat_counts[category] = occurences
            for category in ['flat', 'warmup', 'slowdown', 'no steady state', 'timeout', 'errored']:
                if category not in cat_counts:
                    cat_counts[category] = 0
            # Average information for all process executions.
            if cat_counts['no steady state'] > 0 or cat_counts['timeout'] > 0 or cat_counts['errored']:
                mean_time, error_time = None, None
                median_iter, error_iter = None, None
                median_time_to_steady, error_time_to_steady = None, None
            elif categories_set == set(['flat']):
                median_iter, error_iter = None, None
                median_time_to_steady, error_time_to_steady = None, None
                # Shell out to PyPy for speed.
                marshalled_data = json.dumps(segments_for_bootstrap_all_pexecs)
                mean_time, error_time = bootstrap_runner(marshalled_data, quality)
                if mean_time is None or error_time is None:
                    raise ValueError()
            else:
                # Shell out to PyPy for speed.
                marshalled_data = json.dumps(segments_for_bootstrap_all_pexecs)
                mean_time, error_time = bootstrap_runner(marshalled_data, quality)
                if mean_time is None or error_time is None:
                    raise ValueError()
                if steady_iters:
                    median_iter, error_iter = median_iqr([float(val) for val in steady_iters])
                    median_time_to_steady, error_time_to_steady = median_iqr(time_to_steadys)
                else:  # No changepoints in any process executions.
                    assert False  # Should be handled by elif clause above.
            # Add summary for this benchmark.
            current_benchmark = dict()
            current_benchmark['classification'] = reported_category
            current_benchmark['detailed_classification'] = cat_counts
            current_benchmark['steady_state_iteration'] = median_iter
            current_benchmark['steady_state_iteration_iqr'] = error_iter
            current_benchmark['steady_state_iteration_list'] = steady_iters
            current_benchmark['steady_state_time_to_reach_secs'] = median_time_to_steady
            current_benchmark['steady_state_time_to_reach_secs_iqr'] = error_time_to_steady
            current_benchmark['steady_state_time_to_reach_secs_list'] = time_to_steadys
            current_benchmark['steady_state_time'] = mean_time
            current_benchmark['steady_state_time_ci'] = error_time
            current_benchmark['steady_state_time_list'] = steady_state_means

            pexecs = list()  # This is needed for JSON output.
            for index in xrange(n_pexecs):
                pexecs.append({'index':index, 'classification':categories[index],
                              'outliers':outliers[index], 'changepoints':changepoints[index],
                              'segment_means':segments[index]})
            current_benchmark['process_executons'] = pexecs
            summary_data['machines'][machine][vm][bench] = current_benchmark
    return summary_data


def convert_to_latex(summary_data, delta, steady_state, diff=None, previous=None):
    assert 'warmup_format_version' in summary_data and summary_data['warmup_format_version'] == JSON_VERSION_NUMBER, \
        'Cannot process data from old JSON formats.'
    if (diff and not previous) or (previous and not diff):
        assert False, 'convert_to_latex needs both diff and previous arguments.'
    machine = None
    for key in summary_data['machines']:
        if key == 'warmup_format_version':
            continue
        elif machine is not None:
            assert False, 'Cannot summarise data from more than one machine.'
        else:
            machine = key
    benchmark_names = set()
    latex_summary = dict()
    for vm in summary_data['machines'][machine]:
        latex_summary[vm] = dict()
        for bmark_name in summary_data['machines'][machine][vm]:
            # If a bmark appears in the summary data but was skipped in the
            # 'previous' data, then we do not want it to appear in the diff.
            if diff and bmark_name not in previous['machines'][machine][vm]:
                continue
            bmark = summary_data['machines'][machine][vm][bmark_name]
            benchmark_names.add(bmark_name)
            steady_iter_var, steady_time_var = '', ''
            if bmark['classification'] == 'bad inconsistent':
                reported_category = STYLE_SYMBOLS['bad inconsistent']
                cats_sorted = OrderedDict(sorted(bmark['detailed_classification'].items(),
                                                 key=lambda x: x[1], reverse=True))
                cat_counts = list()
                for category in cats_sorted:
                    if cats_sorted[category] == 0:
                        continue
                    cat_counts.append('$%d$%s' % (cats_sorted[category], STYLE_SYMBOLS[category]))
                reported_category += ' \\scriptsize(%s)' % ', '.join(cat_counts)
            elif bmark['classification'] == 'good inconsistent':
                reported_category = STYLE_SYMBOLS['good inconsistent']
                cats_sorted = OrderedDict(sorted(bmark['detailed_classification'].items(),
                                                 key=lambda x: x[1], reverse=True))
                cat_counts = list()
                for category in cats_sorted:
                    if cats_sorted[category] == 0:
                        continue
                    cat_counts.append('$%d$%s' % (cats_sorted[category], STYLE_SYMBOLS[category]))
                reported_category += ' \\scriptsize(%s)' % ', '.join(cat_counts)
            elif (sum(bmark['detailed_classification'].values()) ==
                  bmark['detailed_classification'][bmark['classification']]):
                # Consistent benchmark with no errors.
                reported_category = STYLE_SYMBOLS[bmark['classification']]
            else:  # No inconsistencies, but some process executions errored.
                reported_category = ' %s\\scriptsize{($%d$)}' % \
                                    (STYLE_SYMBOLS[bmark['classification']],
                                     bmark['detailed_classification'][bmark['classification']])
            if bmark['steady_state_iteration'] is not None:
                change = None
                if diff and diff[vm][bmark_name] and diff[vm][bmark_name][STEADY_ITER] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_iteration']:
                    change = bmark['steady_state_iteration'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_iteration']
                mean_steady_iter = format_median_error(bmark['steady_state_iteration'],
                                                       bmark['steady_state_iteration_iqr'],
                                                       bmark['steady_state_iteration_list'],
                                                       one_dp=True,
                                                       change=change)
                if diff and diff[vm][bmark_name][STEADY_ITER_VAR] and diff[vm][bmark_name][STEADY_ITER_VAR] != 'SAME':
                    was = previous['machines'][machine][vm][bmark_name]['steady_state_iteration_iqr']
                    steady_iter_var = format_median_error(None,
                                                          bmark['steady_state_iteration_iqr'],
                                                          bmark['steady_state_iteration_list'],
                                                          one_dp=True,
                                                          was=was)
            else:
                mean_steady_iter = ''
            if bmark['steady_state_time'] is not None:
                change = None
                if diff and diff[vm][bmark_name] and diff[vm][bmark_name][STEADY_STATE_TIME] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time_ci']:
                    change = bmark['steady_state_time'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time']
                mean_steady = format_median_ci(bmark['steady_state_time'],
                                               bmark['steady_state_time_ci'],
                                               bmark['steady_state_time_list'],
                                               change=change)
                if diff and diff[vm][bmark_name] and diff[vm][bmark_name][STEADY_STATE_TIME_VAR] is not None:
                    change = abs(bmark['steady_state_time_ci'] - previous['machines'][machine][vm][bmark_name]['steady_state_time_ci'])
                    steady_time_var = "$\\begin{array}{c}\\scriptstyle{%.5f}\\\\[-6pt]\n\\scriptscriptstyle{was: %.5f}\n\\end{array}$" % (bmark['steady_state_time_ci'], previous['machines'][machine][vm][bmark_name]['steady_state_time_ci'])
            else:
                mean_steady = ''
            if bmark['steady_state_time_to_reach_secs'] is not None:
                change = None
                if diff and diff[vm][bmark_name] and diff[vm][bmark_name][STEADY_ITER] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time_to_reach_secs']:
                    change = bmark['steady_state_time_to_reach_secs'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time_to_reach_secs']
                time_to_steady = format_median_error(bmark['steady_state_time_to_reach_secs'],
                                                     bmark['steady_state_time_to_reach_secs_iqr'],
                                                     bmark['steady_state_time_to_reach_secs_list'],
                                                     two_dp=True,
                                                     change=change)
            else:
                time_to_steady = ''
            latex_summary[vm][bmark_name] = {'style': reported_category,
                'last_cpt': mean_steady_iter, 'last_mean': mean_steady,
                'time_to_steady_state': time_to_steady,
                'steady_iter_var': steady_iter_var,
                'steady_time_var': steady_time_var}
    return machine, list(sorted(benchmark_names)), latex_summary


def write_latex_table(machine, all_benchs, summary, tex_file, with_preamble=False,
                      longtable=False, only_vms=None):
    """Write a tex table to disk.
    This is NOT used to create diff tables or the tables for the warmup
    experiment (a separate script in the other repo exists for that). However,
    we need to factor this as a separate function so that it can be imported
    by `../bin/warmup_stats` and `../bin/table_classification_summaries_others`.
    """

    num_benchmarks = len(all_benchs)
    if only_vms is not None:
        all_vms = sorted([vm for vm in summary.keys() if vm in only_vms])
    else:
        all_vms = sorted(summary.keys())
    num_vms = len(summary)

    with open(tex_file, 'w') as fp:
        if with_preamble:
            fp.write(preamble(TITLE))
            fp.write('\\centering %s' % get_latex_symbol_map())
            fp.write('\n\n\n')
            if not longtable:
                fp.write('\\begin{landscape}\n')
                fp.write('\\begin{table*}[hptb]\n')
                fp.write('\\vspace{.8cm}\n')
                fp.write('\\begin{adjustbox}{totalheight=12.4cm}\n')
        heads1 = TABLE_HEADINGS_START1 + '&'.join([TABLE_HEADINGS1])
        heads2 = TABLE_HEADINGS_START2 + '&'.join([TABLE_HEADINGS2])
        heads = '%s\\\\%s' % (heads1, heads2)
        if longtable:
            fp.write(start_longtable(TABLE_FORMAT, heads))
        else:
            fp.write(start_table(TABLE_FORMAT, heads))
        split_row_idx = 0
        for vm in all_vms:
            bench_idx = 0
            for bench in sorted(all_benchs):
                row = []
                if vm is None:
                    continue # no more results
                try:
                    this_summary = summary[vm][bench]
                except KeyError:
                    last_cpt = BLANK_CELL
                    time_steady = BLANK_CELL
                    last_mean = BLANK_CELL
                    classification = ''
                else:
                    classification = this_summary['style']
                    last_cpt = this_summary['last_cpt']
                    time_steady = this_summary['time_to_steady_state']
                    last_mean = this_summary['last_mean']

                    classification = '\\multicolumn{1}{l}{%s}' % classification
                    if classification == STYLE_SYMBOLS['flat']:
                        last_cpt = BLANK_CELL
                        time_steady = BLANK_CELL
                if last_cpt == '':
                    last_cpt = BLANK_CELL
                if time_steady == '':
                    time_steady = BLANK_CELL
                if last_mean == '':
                    last_mean = BLANK_CELL

                if bench_idx == 0:
                    if num_benchmarks == 10:
                        fudge = 4
                    elif num_benchmarks == 12:
                        fudge = 5
                    else:
                        fudge = 0
                    vm_cell = '\\multirow{%s}{*}{\\rotatebox[origin=c]{90}{%s}}' \
                        % (num_benchmarks + fudge, vm)
                else:
                    vm_cell = ''
                row_add = [BLANK_CELL, vm_cell, classification, last_cpt,
                           time_steady, last_mean]
                row.insert(0, escape(bench))
                row.extend(row_add)
                fp.write('&'.join(row))
                # Only -ve space row if not next to a midrule
                if not longtable and bench_idx < num_vms - 1:
                    fp.write('\\\\[-3pt] \n')
                else:
                    fp.write('\\\\ \n')
                bench_idx += 1
            if split_row_idx < num_vms - 1:
                if longtable:
                    fp.write('\\hline\n')
                else:
                    fp.write('\\midrule\n')
            split_row_idx += 1
        if longtable:
            fp.write(end_longtable())
        else:
            fp.write(end_table())
        if with_preamble:
            if not longtable:
                fp.write('\\end{adjustbox}\n')
                fp.write('\\end{table*}\n')
                fp.write('\\end{landscape}\n')
            fp.write(end_document())


def colour_html_cell(result, text, align=None):
    """Colour a table cell containing `text` according to `result`."""

    assert result in (None, SAME, DIFFERENT, BETTER, WORSE)
    if result == BETTER:
        colour = 'id="lightgreen"'
    elif result == WORSE:
        colour = 'id="lightred"'
    elif result == DIFFERENT:
        colour = 'id="lightyellow"'
    else:
        colour = ''
    if align:
        text_align = 'style="text-align: %s;"' % align
    else:
        text_align = ''
    return '<td %s %s>%s</td>' % (text_align, colour, text)


def htmlify_histogram(nth):
    return '<div id="bar%d" class="histogram"></div>' % nth


def write_html_table(summary_data, html_filename, diff=None, skipped=None, previous=None):
    assert 'warmup_format_version' in summary_data and summary_data['warmup_format_version'] == JSON_VERSION_NUMBER, \
        'Cannot process data from old JSON formats.'
    machine = None
    for key in summary_data['machines']:
        if key == 'warmup_format_version':
            continue
        elif machine is not None:
            assert False, 'Cannot summarise data from more than one machine.'
        else:
            machine = key
    html_table_contents = dict()  # VM name -> html rows
    n_charts = 0
    histograms = ''  # Javascript.
    for vm in sorted(summary_data['machines'][machine]):
        html_rows = ''  # Just the table rows, no table header, etc.
        if skipped is not None:
            skipped_before = [b for (b, v) in skipped[SKIPPED_BEFORE] if v == vm]
        else:
            skipped_before = []
        if skipped is not None:
            skipped_after = [b for (b, v) in skipped[SKIPPED_AFTER] if v == vm]
        else:
            skipped_after = []
        for bmark_name in sorted(summary_data['machines'][machine][vm].keys()):
            bmark = summary_data['machines'][machine][vm][bmark_name]
            # If a bmark appears in the summary data but was skipped in the
            # 'previous' data, then we do not want it to appear in the diff.
            if diff and bmark_name not in previous['machines'][machine][vm]:
                if bmark_name in skipped_before or bmark_name in skipped_after:
                    if diff and vm in diff and bmark_name in diff[vm]:
                        bmark_cell = colour_html_cell(diff[vm][bmark_name][INTERSECTION], bmark_name)
                    else:
                        bmark_cell = '<td>%s</td>' % bmark_name
                    category_cell = '<td><em>Skipped</em></td>'
                    blank_cell = '<td></td>'
                    if diff:
                        # Benchmark name, classification, steady iter, steady iter variation, time to reach,
                        # steady perf, steady perf variation
                        row = ('<tr>%s%s%s%s%s%s%s</tr>\n' %
                               (bmark_cell, category_cell, blank_cell, blank_cell, blank_cell,
                                blank_cell, blank_cell))
                    else:
                        # Benchmark name, classification, steady iter, time to reach, steady perf
                        row = ('<tr>%s%s%s%s%s</tr>\n' %
                               (bmark_cell, category_cell, blank_cell, blank_cell, blank_cell))
                    html_rows += row
                continue
            if bmark['classification'] == 'bad inconsistent':
                reported_category = get_symbol('bad inconsistent')
                cats_sorted = OrderedDict(sorted(bmark['detailed_classification'].items(),
                                                 key=lambda x: x[1], reverse=True))
                cat_counts = list()
                for category in cats_sorted:
                    if cats_sorted[category] == 0:
                        continue
                    cat_counts.append('%d %s' % (cats_sorted[category], get_symbol(category)))
                reported_category += ' (%s)' % ', '.join(cat_counts)
            elif bmark['classification'] == 'good inconsistent':
                reported_category = get_symbol('good inconsistent')
                cats_sorted = OrderedDict(sorted(bmark['detailed_classification'].items(),
                                                 key=lambda x: x[1], reverse=True))
                cat_counts = list()
                for category in cats_sorted:
                    if cats_sorted[category] == 0:
                        continue
                    cat_counts.append('%d %s' % (cats_sorted[category], get_symbol(category)))
                reported_category += ' (%s)' % ', '.join(cat_counts)
            elif (sum(bmark['detailed_classification'].values()) ==
                  bmark['detailed_classification'][bmark['classification']]):
                # Consistent benchmark with no errors.
                reported_category = get_symbol(bmark['classification'])
            else:  # No inconsistencies, but some process executions errored.
                reported_category = ' %s %d' % (get_symbol(bmark['classification']),
                                     bmark['detailed_classification'][bmark['classification']])
            if diff and vm in diff and bmark_name in diff[vm]:
                category_cell = colour_html_cell(diff[vm][bmark_name][CLASSIFICATIONS], reported_category)
            else:
                category_cell = '<td>%s</td>' % reported_category
            if bmark['steady_state_iteration'] is not None:
                change = ''
                histograms += html_histogram(bmark['steady_state_iteration_list'], n_charts)
                if diff and vm in diff and bmark_name in diff[vm] and diff[vm][bmark_name][STEADY_ITER] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_iteration']:
                    delta = bmark['steady_state_iteration'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_iteration']
                    change = '<br/><small>&delta;=%.1f</small>' % delta
                mean_steady_iter = '%s<div class="wrapper"><div class="tdcenter">%.1f%s<br/><small>(%.1f, %.1f)</small></div></div>' % \
                    (htmlify_histogram(n_charts), bmark['steady_state_iteration'], change,
                     bmark['steady_state_iteration_iqr'][0], bmark['steady_state_iteration_iqr'][1])
                if diff and vm in diff and bmark_name in diff[vm]:
                    mean_steady_iter_cell = colour_html_cell(diff[vm][bmark_name][STEADY_ITER], mean_steady_iter, 'center')
                    if diff[vm][bmark_name][STEADY_ITER_VAR] and diff[vm][bmark_name][STEADY_ITER_VAR] != 'SAME':
                        var = '<div class="wrapper"><div class="tdcenter">(%.1f, %.1f)</br><small>was:&nbsp;(%.1f, %.1f)</small></div></div>' % \
                                   (bmark['steady_state_iteration_iqr'][0], bmark['steady_state_iteration_iqr'][1],
                                    previous['machines'][machine][vm][bmark_name]['steady_state_iteration_iqr'][0],
                                    previous['machines'][machine][vm][bmark_name]['steady_state_iteration_iqr'][1])
                        mean_steady_iter_var_cell = colour_html_cell(diff[vm][bmark_name][STEADY_ITER_VAR], var, 'center')
                    else:
                        mean_steady_iter_var_cell = '<td></td>'
                else:
                    mean_steady_iter_cell = '<td style="text-align: center;">%s</td>' % mean_steady_iter
                n_charts += 1
            else:
                mean_steady_iter_cell = '<td></td>'
                if diff:
                    mean_steady_iter_var_cell = '<td></td>'
            if bmark['steady_state_time'] is not None:
                change = ''
                histograms += html_histogram(bmark['steady_state_time_list'], n_charts)
                if diff and vm in diff and bmark_name in diff[vm] and diff[vm][bmark_name][STEADY_STATE_TIME] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time']:
                    delta = bmark['steady_state_time'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time']
                    change = '<br/><small>&delta;=%.5f</small>' % delta
                mean_steady = '%s<div class="wrapper"><div class="tdright">%.5f%s<br/><small>&plusmn;%.6f</small></div></div>' % \
                        (htmlify_histogram(n_charts), bmark['steady_state_time'], change, bmark['steady_state_time_ci'])
                if diff and vm in diff and bmark_name in diff[vm]:
                    mean_steady_cell = colour_html_cell(diff[vm][bmark_name][STEADY_STATE_TIME], mean_steady, 'right')
                    if diff[vm][bmark_name][STEADY_STATE_TIME_VAR] and diff[vm][bmark_name][STEADY_STATE_TIME_VAR] != 'SAME':
                        var = '<div class="wrapper"><div class="tdcenter">%.6f<br/><small>was: %.6f</small></div></div>' % \
                                   (bmark['steady_state_time_ci'],
                                    previous['machines'][machine][vm][bmark_name]['steady_state_time_ci'])
                        mean_steady_var_cell = colour_html_cell(diff[vm][bmark_name][STEADY_STATE_TIME_VAR], var, 'center')
                    else:
                        mean_steady_var_cell = '<td></td>'
                else:
                    mean_steady_cell = '<td style="text-align: right;">%s</td>' % mean_steady
                n_charts += 1
            else:
                mean_steady_cell = '<td></td>'
                if diff:
                    mean_steady_var_cell = '<td></td>'
            if bmark['steady_state_time_to_reach_secs'] is not None:
                change = ''
                histograms += html_histogram(bmark['steady_state_time_to_reach_secs_list'], n_charts)
                if diff and vm in diff and bmark_name in diff[vm] and diff[vm][bmark_name][STEADY_ITER] != SAME and \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time_to_reach_secs']:
                    delta = bmark['steady_state_time_to_reach_secs'] - \
                        previous['machines'][machine][vm][bmark_name]['steady_state_time_to_reach_secs']
                    change = '<br/><small>&delta;=%.3f</small>' % delta
                time_to_steady = '%s<div class="wrapper"><div class="tdcenter">%.3f%s<br/><small>(%.3f, %.3f)</small></div></div>' \
                        % (htmlify_histogram(n_charts), bmark['steady_state_time_to_reach_secs'],
                           change, bmark['steady_state_time_to_reach_secs_iqr'][0], bmark['steady_state_time_to_reach_secs_iqr'][1])
                if diff and vm in diff and bmark_name in diff[vm]:
                    time_steady_cell = colour_html_cell(diff[vm][bmark_name][STEADY_ITER], time_to_steady, 'center')
                else:
                    time_steady_cell = '<td style="text-align: center;">%s</td>' % time_to_steady
                n_charts += 1
            else:
                time_steady_cell = '<td></td>'
            if diff and vm in diff and bmark_name in diff[vm]:
                bmark_cell = colour_html_cell(diff[vm][bmark_name][INTERSECTION], bmark_name)
            else:
                bmark_cell = '<td>%s</td>' % bmark_name
            if diff:
                # Benchmark name, classification, steady iter, steady iter variation, time to reach,
                # steady perf, steady perf variation
                row = ('<tr>%s%s%s%s%s%s%s</tr>\n' %
                       (bmark_cell, category_cell, mean_steady_iter_cell, mean_steady_iter_var_cell, time_steady_cell,
                        mean_steady_cell, mean_steady_var_cell))
            else:
                # Benchmark name, classification, steady iter, time to reach, steady perf
                row = ('<tr>%s%s%s%s%s</tr>\n' %
                       (bmark_cell, category_cell, mean_steady_iter_cell, time_steady_cell, mean_steady_cell))
            html_rows += row
        html_table_contents[vm] = html_rows
    page_contents = ''
    if diff:
        page_contents += DIFF_LEGEND
        table_template = HTML_DIFF_TABLE_TEMPLATE
    else:
        table_template = HTML_TABLE_TEMPLATE
    for vm in html_table_contents:
        page_contents += table_template % (vm, html_table_contents[vm])
        page_contents += '\n\n'
        page_contents += histograms + '\n\n'
    page_contents += HTML_SYMBOLS + '\n\n'
    with open(html_filename, 'w') as fp:
        fp.write(HTML_PAGE_TEMPLATE % page_contents)
