from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd
from copy import deepcopy
import numpy as np
from scipy.integrate import simps
from os.path import sep
from os import makedirs
import csv
import matplotlib.pyplot as plt
from matplotlib import patches, colormaps
import json
import platform
from helpers.format_axes import format_ax
from helpers.preprocess_files import preprocess_files

# Tweak the regex file separator for cross-platform compatibility
if platform.system() == 'Windows':
    REGEX_SEP = sep * 2
else:
    REGEX_SEP = sep


class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


def __get_trialID_zscore(processed_signal, key_times_df,
                         baseline_start_for_zscore=0., baseline_end_for_zscore=1.,
                         response_window_duration=4.,
                         response_latency_filter=False,
                         align_to_response=False,
                         subtract_405=True):
    ret_list = list()
    # There are inconsistencies between the Aversive and Appetitive paradigm in the column naming.
    # Make all lower case here

    key_times_df.columns = key_times_df.columns.str.lower()
    for _, cur_trial in key_times_df.iterrows():
        # remove reminders
        if cur_trial[cur_trial.keys().str.contains('reminder')].iloc[0] == 1:
            continue

        # Only include trials with higher than 0.8 s response time
        # 0.4 is the start of the AM
        # Assume animals did not wait for signal otherwise
        if cur_trial[cur_trial.keys().str.contains('resplatency')].iloc[0] / 1000 < response_latency_filter:
            continue

        # Use response time to get at the reward delivery
        if align_to_response:
            response_time = cur_trial[cur_trial.keys().str.contains('resplatency')].iloc[0] / 1000
            if np.isnan(response_time):
                continue
        else:
            response_time = 0

        signal_around_trial = processed_signal[
            (processed_signal['Time'] > (cur_trial['trial_onset'] - baseline_start_for_zscore + response_time)) &
            (processed_signal['Time'] <= (cur_trial['trial_onset'] + response_window_duration + response_time))]

        # 405 fit-removed signal
        if subtract_405:
            sig_column = 'Ch465_dff'
        else:
            sig_column = 'Ch465_mV'

        # z-score it
        baseline_signal = processed_signal[
            (processed_signal['Time'] > (cur_trial['trial_onset'] - baseline_start_for_zscore + response_time)) &
            (processed_signal['Time'] <= (cur_trial['trial_onset'] - baseline_end_for_zscore + response_time))][
            sig_column]

        # # Truncate signal at response
        # if truncate_at_responseTime:
        #     response_time = cur_trial[cur_trial.keys().str.contains('resplatency')].iloc[0] / 1000
        #     signal_around_trial[signal_around_trial['Time'] >= (cur_trial['trial_onset'] + response_time)] = np.NaN

        baseline_mean = np.nanmean(baseline_signal)
        baseline_std = np.nanstd(baseline_signal)

        dff_zscore = (signal_around_trial[sig_column].values - baseline_mean) / baseline_std

        # Trial parameters will be under index 0, signal will always be index 1
        # Convert amdepth to dB and round
        cur_amdepth = cur_trial[cur_trial.keys().str.contains('amdepth')].iloc[0]
        if cur_amdepth > 0:
            cur_amdepth = np.round(20 * np.log10(cur_amdepth), 1)
        else:
            cur_amdepth = -40
        ret_list.append(((cur_trial['trialid'], cur_amdepth,
                          cur_trial['trial_onset'],
                          cur_trial['trial_offset']),
                         dff_zscore))
    return ret_list


def __calculate_PeakValue_and_AUC(sigs, trial_info, baseline_window_start_time, trial_type,
                                  fs, x_axis,
                                  auc_start=0, auc_end=4):
    auc_response = np.zeros(np.shape(sigs)[0])
    peak = np.zeros(np.shape(sigs)[0])
    auc_baseline = np.zeros(np.shape(sigs)[0])
    ret_trial_info = deepcopy(trial_info)  # Make a copy in case this needs to be modified
    for trial_idx, cur_trial in enumerate(ret_trial_info):
        bounded_response_xaxis = x_axis[int((auc_start + baseline_window_start_time) * fs):
                                        int((auc_end + baseline_window_start_time) * fs)]

        bounded_response = sigs[trial_idx, int((auc_start + baseline_window_start_time) * fs):
                                           int((auc_end + baseline_window_start_time) * fs)]

        bounded_baseline_xaxis = x_axis[0:int(baseline_window_start_time * fs)]
        bounded_baseline = sigs[trial_idx, 0:int(baseline_window_start_time * fs)]

        auc_response[trial_idx] = simps(bounded_response, bounded_response_xaxis)
        peak[trial_idx] = np.max(bounded_response)
        auc_baseline[trial_idx] = simps(bounded_baseline, bounded_baseline_xaxis)

    return ret_trial_info, auc_response, peak, auc_baseline


def run_zscore_extraction(input_list):
    (session_date_paths, SETTINGS_DICT) = input_list

    # Load globals
    keys_path = SETTINGS_DICT['KEYS_PATH']
    baseline_start_for_zscore = SETTINGS_DICT['BASELINE_START_FOR_ZSCORE']
    baseline_end_for_zscore = SETTINGS_DICT['BASELINE_END_FOR_ZSCORE']
    response_window_duration = SETTINGS_DICT['RESPONSE_WINDOW_DURATION']
    subtract_405 = SETTINGS_DICT['SUBTRACT_405']
    auc_start = SETTINGS_DICT['AUC_WINDOW_START']
    auc_end = SETTINGS_DICT['AUC_WINDOW_END']

    target_sound_onset = SETTINGS_DICT['TARGET_SOUND_ONSET']
    target_sound_offset = SETTINGS_DICT['TARGET_SOUND_OFFSET']

    output_path = SETTINGS_DICT['OUTPUT_PATH']
    trial_zscore_plots_path = output_path + sep + 'Aligned signals'
    makedirs(trial_zscore_plots_path, exist_ok=True)

    response_latency_filter = SETTINGS_DICT['RESPONSE_LATENCY_FILTER']

    # Plot colors and some parameters
    passive_color = 'black'
    hit_color = '#60B2E5'
    fa_color = 'goldenrod'
    miss_color = '#C84630'

    # Specific to Aversive paradigm
    hitShock_color = hit_color
    hitNoShock_color = hit_color
    missShock_color = miss_color
    missNoShock_color = miss_color

    # Specific to 1IFC
    reject_color = '#2B4570'

    # Run this twice, once aligning to trial onset; another aligning to spout offset (aversive) OR reward trigger (1IFC)
    run_modes = ['trial_aligned', 'response_aligned']

    for run_mode in run_modes:
        if run_mode == 'trial_aligned':
            align_to_response = False
        else:
            align_to_response = True

        trial_type_dict = dict()
        fs = 0
        subj_date = ''
        for recording_path in session_date_paths:
            subj_date, info_key_times, spout_key_times, trial_types = preprocess_files(recording_path, SETTINGS_DICT)

            # Reset this here, because passive files will change this to 0
            response_latency_filter = SETTINGS_DICT['RESPONSE_LATENCY_FILTER']

            # Load signal
            processed_signal = pd.read_csv(recording_path)

            # If not set, approximate sampling rate
            if SETTINGS_DICT['SAMPLING_RATE'] is None:
                fs = 1 / np.mean(np.diff(processed_signal['Time']))
            else:
                fs = SETTINGS_DICT['SAMPLING_RATE']

            for trial_type in trial_types:
                if '1IFC' in recording_path or '1IFC' in SETTINGS_DICT['EXPERIMENT_TYPE']:
                    if trial_type == 'Hit':
                        cur_key_times = info_key_times[(info_key_times['Hit'] == 1)]
                    elif trial_type == 'Miss':
                        cur_key_times = info_key_times[(info_key_times['Miss'] == 1)]
                    elif trial_type == 'Reject':
                        cur_key_times = info_key_times[(info_key_times['CR'] == 1)]
                    elif trial_type == 'False alarm':
                        cur_key_times = info_key_times[(info_key_times['FA'] == 1)]
                    else:  # Passive
                        cur_key_times = info_key_times[(info_key_times['TrialType'] == 0)]
                        # Keep track of trial number and onset time too
                elif ('Aversive' in recording_path or 'Passive' in recording_path
                        or 'AversiveAM' in SETTINGS_DICT['EXPERIMENT_TYPE']):
                    if trial_type == 'Hit (shock)':
                        cur_key_times = info_key_times[
                            (info_key_times['TrialType'] == 0) & (info_key_times['Hit'] == 1) & (
                                    info_key_times['Reminder'] == 0) &
                            (info_key_times['ShockFlag'] == 1)]

                    elif trial_type == 'Hit (no shock)':
                        cur_key_times = info_key_times[
                            (info_key_times['TrialType'] == 0) & (info_key_times['Hit'] == 1) & (
                                    info_key_times['Reminder'] == 0) &
                            (info_key_times['ShockFlag'] == 0)]

                    elif trial_type == 'Miss (shock)':
                        cur_key_times = info_key_times[
                            (info_key_times['TrialType'] == 0) & (info_key_times['Miss'] == 1) & (
                                    info_key_times['Reminder'] == 0) &
                            (info_key_times['ShockFlag'] == 1)]

                    elif trial_type == 'Miss (no shock)':
                        cur_key_times = info_key_times[
                            (info_key_times['TrialType'] == 0) & (info_key_times['Miss'] == 1) & (
                                    info_key_times['Reminder'] == 0) &
                            (info_key_times['ShockFlag'] == 0)]

                    elif trial_type == 'False alarm':
                        cur_key_times = info_key_times[
                            (info_key_times['FA'] == 1) & (info_key_times['Reminder'] == 0)]

                    else:  # Passive
                        cur_key_times = info_key_times[(info_key_times['TrialType'] == 0)]
                        response_latency_filter = 0
                else:
                    print('Experiment type not recognized. Exiting.')
                    return
                cur_signals = __get_trialID_zscore(processed_signal, cur_key_times,
                                                   baseline_start_for_zscore=baseline_start_for_zscore,
                                                   baseline_end_for_zscore=baseline_end_for_zscore,
                                                   response_window_duration=response_window_duration,
                                                   response_latency_filter=response_latency_filter,
                                                   align_to_response=align_to_response,
                                                   subtract_405=subtract_405)

                trial_type_dict.update({trial_type: cur_signals})


        # uniformize lengths and exclude truncated signals by more than half sampling rate points
        # The median length should be the target
        tolerance = fs / 2
        sig_lengths = []
        for trial_type_key in trial_type_dict.keys():
            sig_lengths.extend([len(x[1]) for x in trial_type_dict[trial_type_key]])
        median_length = np.median(sig_lengths)

        sig_lengths = []
        for trial_type_key in trial_type_dict.keys():
            trial_type_dict[trial_type_key] = [x for
                                               x in trial_type_dict[trial_type_key] if
                                               (len(x[1]) > (median_length - tolerance)) and
                                               (len(x[1]) < (median_length + 100))]

            sig_lengths.extend([len(x[1]) for x in trial_type_dict[trial_type_key]])

        # Check if there are any signals present
        if len(sig_lengths) == 0:
            print('No signals found. Tip: did you set the response latency filter properly?')
            return

        # Now uniformize lengths (tolerated jitter of 1 point)
        min_length = np.median(sig_lengths)
        for trial_type_key in trial_type_dict.keys():
            trial_type_dict[trial_type_key] = [(x[0], np.array(x[1][0:int(min_length)])) for
                                               x in trial_type_dict[trial_type_key]]

        # Now plot combining passive and task in one plot and CSV

        output_dict = dict()
        sig_mean_dict = dict()

        # Select specific AMs
        ams_to_analyze = None  # or None for all

        if align_to_response:
            file_name = subj_date + '_responseAligned_trialSummary'
        else:
            file_name = subj_date + '_trialAligned_trialSummary'
        if SETTINGS_DICT['PIPELINE_SWITCHBOARD']['plot_trial_zscores']:
            with PdfPages(sep.join([trial_zscore_plots_path, file_name + '.pdf'])) as pdf:
                # fig, ax = plt.subplots(1, 1)
                fig = plt.figure()
                ax = fig.add_subplot(111)

                # Trial onset or shading
                if align_to_response:
                    ax.axvline(0, linestyle='--', color='black')
                else:
                    ax.axvspan(target_sound_onset, target_sound_offset, ymin=0.05, ymax=0.075,
                               facecolor='black', alpha=0.25)

                    if '1IFC' in recording_path or '1IFC' in SETTINGS_DICT['EXPERIMENT_TYPE']:
                        ax.axvspan(response_latency_filter, response_window_duration,
                                   ymin=0.025, ymax=0.05, facecolor='g', alpha=0.25)

                legend_handles = list()
                for trial_type in trial_type_dict.keys():
                    # Map to color
                    if trial_type == 'Hit':
                        cur_color = hit_color
                    elif trial_type == 'Hit (shock)':
                        cur_color = hitShock_color
                    elif trial_type == 'Hit (no shock)':
                        cur_color = hitNoShock_color
                    elif trial_type == 'Miss':
                        cur_color = miss_color
                    elif trial_type == 'Miss (shock)':
                        cur_color = missShock_color
                    elif trial_type == 'Miss (no shock)':
                        cur_color = missNoShock_color
                    elif trial_type == 'False alarm':
                        cur_color = fa_color
                    elif trial_type == 'Reject':
                        cur_color = reject_color
                    else:  # Passive
                        cur_color = passive_color

                    sigs = np.zeros((len(trial_type_dict[trial_type]), int(min_length)))
                    for i, ts in enumerate(trial_type_dict[trial_type]):
                        if ams_to_analyze is not None:
                            if ts[0][1] in ams_to_analyze:
                                sigs[i, 0:len(ts[1])] = ts[1]
                            else:
                                continue
                        else:
                            sigs[i, 0:len(ts[1])] = ts[1]
                    if np.size(sigs) == 0:
                        continue

                    if trial_type == 'Miss (no shock)' or trial_type == 'Hit (no shock)':
                        linestyle = '--'
                    else:
                        linestyle = '-'

                    # If you ever need to downsample, this is how you do it
                    # downsample_q = 100
                    # plot_sigs = resample(sigs, np.shape(sigs)[1]//downsample_q, axis=1)
                    # plot_sigs = np.array([convolve_fft(sig, Gaussian1DKernel(stddev=10), preserve_nan=True) for sig in sigs])

                    plot_sigs = sigs

                    signals_mean = np.nanmean(plot_sigs, axis=0)
                    signals_sem = np.nanstd(plot_sigs, axis=0) / np.sqrt(np.count_nonzero(~np.isnan(plot_sigs), axis=0))
                    x_axis = np.linspace(-baseline_start_for_zscore, response_window_duration, len(signals_mean))
                    ax.plot(x_axis, signals_mean, color=cur_color, linestyle=linestyle)
                    ax.fill_between(x_axis, signals_mean - signals_sem, signals_mean + signals_sem,
                                    alpha=0.1, color=cur_color, edgecolor='none')

                    legend_handles.append(patches.Patch(facecolor=cur_color, edgecolor=None, alpha=0.5,
                                                        label=trial_type))

                    # Measure and add measurements to list
                    trial_info = [x[0] for x in trial_type_dict[trial_type]]  # idx=0
                    trial_info, auc_response, peak, auc_baseline = __calculate_PeakValue_and_AUC(sigs,
                                                                                                 trial_info,
                                                                                                 trial_type=trial_type,
                                                                                                 baseline_window_start_time=baseline_start_for_zscore,
                                                                                                 fs=fs, x_axis=np.linspace(-baseline_start_for_zscore, response_window_duration,  np.shape(sigs)[1]),
                                                                                                 auc_start=auc_start,
                                                                                                 auc_end=auc_end)

                    output_dict.update({trial_type: (trial_info, auc_response, peak, auc_baseline)})
                    sig_mean_dict.update({trial_type: (x_axis, signals_mean, signals_sem)})

                format_ax(ax)

                ax.set_xlabel("Time from trial onset (s)")
                ax.set_ylabel(r'($\Delta$F/F z-score)')

                # Might want to make this a variable
                # ax.set_ylim([-5, 10])

                labels = [h.get_label() for h in legend_handles]

                fig.legend(handles=legend_handles, labels=labels, frameon=False, numpoints=1, bbox_to_anchor=[0.95, 0.95])

                fig.tight_layout()

                # plt.show()
                pdf.savefig()
                plt.close()
        else:
            for trial_type in trial_type_dict.keys():
                sigs = np.zeros((len(trial_type_dict[trial_type]), int(min_length)))
                for i, ts in enumerate(trial_type_dict[trial_type]):
                    if ams_to_analyze is not None:
                        if ts[0][1] in ams_to_analyze:
                            sigs[i, 0:len(ts[1])] = ts[1]
                        else:
                            continue
                    else:
                        sigs[i, 0:len(ts[1])] = ts[1]
                if np.size(sigs) == 0:
                    continue

                # Measure and add measurements to list
                trial_info = [x[0] for x in trial_type_dict[trial_type]]
                x_axis = np.linspace(-baseline_start_for_zscore, response_window_duration, np.shape(sigs)[1])  # just in case
                trial_info, auc_response, peak, auc_baseline = __calculate_PeakValue_and_AUC(sigs,
                                                                                             trial_info,
                                                                                             trial_type=trial_type,
                                                                                             baseline_window_start_time=baseline_start_for_zscore,
                                                                                             fs=fs,
                                                                                             x_axis=x_axis,
                                                                                             auc_start=auc_start,
                                                                                             auc_end=auc_end)

                output_dict.update({trial_type: (trial_info, auc_response, peak, auc_baseline)})
                sig_mean_dict.update({trial_type: (x_axis, signals_mean, signals_sem)})

        # Plot responses split by AM depth
        if SETTINGS_DICT['PIPELINE_SWITCHBOARD']['plot_AMDepth_zscores']:
            # Gather all AMs presented for use in the plotting
            if ams_to_analyze is None:
                all_ams = list()
                for trial_type_key in trial_type_dict.keys():
                    all_ams.extend(list(set([x[0][1] for x in trial_type_dict[trial_type_key]])))
            else:
                all_ams = ams_to_analyze

            if align_to_response:
                file_name = subj_date + '_responseAligned_byAMdepth'
            else:
                file_name = subj_date + '_trialAligned_byAMdepth'
            with PdfPages(sep.join([trial_zscore_plots_path, file_name + '.pdf'])) as pdf:

                # Trial grouping for plotting, if you'd like to combine responses
                # Example: trial_groups = [('Hit', 'Reject'), ('Miss', 'False alarm')]
                if ('Aversive' in recording_path or 'Passive' in recording_path
                        or 'AversiveAM' in SETTINGS_DICT['EXPERIMENT_TYPE']):
                    # trial_groups = trial_type_dict.keys()
                    trial_groups = [('Hit (shock)', 'Hit (no shock)'), ('Miss (shock)', 'Miss (no shock)'), 'False alarm']
                elif '1IFC' in SETTINGS_DICT['EXPERIMENT_TYPE']:
                    trial_groups = ['Hit', 'Reject', 'Miss', 'False alarm']
                else:
                    print('Experiment type not found. Skipping plotting')
                    break

                for tgroup in trial_groups:
                    fig = plt.figure()
                    ax = fig.add_subplot(111)

                    # Trial onset or shading
                    if align_to_response:
                        ax.axvline(0, linestyle='--', color='black')
                    else:
                        ax.axvspan(target_sound_onset, target_sound_offset, ymin=0.05, ymax=0.075,
                                   facecolor='black', alpha=0.25)

                        if '1IFC' in recording_path or '1IFC' in SETTINGS_DICT['EXPERIMENT_TYPE']:
                            ax.axvspan(response_latency_filter, response_window_duration,
                                       ymin=0.025, ymax=0.05, facecolor='g', alpha=0.25)

                    # will just be each trial type for aversive
                    cur_trialTypes = list(ttype for ttype in trial_type_dict.keys() if ttype in tgroup)
                    legend_handles = list()
                    for trial_type in cur_trialTypes:
                        for amdepth in sorted(list(set(all_ams)), reverse=True):
                            if amdepth > -40:
                                perc_value = np.round(10 ** (amdepth / 20), 2)
                            else:
                                perc_value = 1

                            cmap_factor = perc_value

                            cur_color = colormaps.get_cmap('plasma')(int(cmap_factor * 255))

                            sigs = np.zeros((len(trial_type_dict[trial_type]), int(min_length)))
                            for i, ts in enumerate(trial_type_dict[trial_type]):
                                if ts[0][1] == amdepth:
                                    sigs[i, 0:len(ts[1])] = ts[1]
                                else:
                                    continue

                            if np.size(sigs) == 0 or np.sum(sigs) == 0:
                                continue

                            # If you ever need to downsample, this is how you do it
                            # downsample_q = 100
                            # plot_sigs = resample(sigs, np.shape(sigs)[1]//downsample_q, axis=1)
                            # plot_sigs = np.array([convolve_fft(sig, Gaussian1DKernel(stddev=10), preserve_nan=True) for sig in sigs])

                            plot_sigs = sigs

                            signals_mean = np.nanmean(plot_sigs, axis=0)
                            signals_sem = np.nanstd(plot_sigs, axis=0) / np.sqrt(
                                np.count_nonzero(~np.isnan(plot_sigs), axis=0))
                            x_axis = np.linspace(-baseline_start_for_zscore, response_window_duration,
                                                 len(signals_mean))
                            ax.plot(x_axis, signals_mean, color=cur_color, linestyle=linestyle, alpha=1)
                            ax.fill_between(x_axis, signals_mean - signals_sem, signals_mean + signals_sem,
                                            alpha=0.1, color=cur_color, edgecolor='none')

                            legend_handles.append(patches.Patch(facecolor=cur_color, edgecolor=None, alpha=1,
                                                                label=str(amdepth) + ' dB'))

                    format_ax(ax)

                    ax.set_xlabel("Time from trial onset (s)")
                    ax.set_ylabel(r'$\Delta$F/F z-score')

                    # Might want to make this a variable
                    # ax.set_ylim([-1, 1])

                    labels = [h.get_label() for h in legend_handles]

                    fig.legend(handles=legend_handles, labels=labels, frameon=False, numpoints=1,
                               bbox_to_anchor=[0.95, 0.95])

                    # Plot title
                    if all(['Hit' in temp_ttype for temp_ttype in tgroup]):
                        fig.suptitle('Hit trials')
                    elif all(['Miss' in temp_ttype for temp_ttype in tgroup]):
                        fig.suptitle('Miss trials')
                    else:
                        if isinstance(tgroup, str):
                            fig.suptitle(tgroup)
                        else:
                            fig.suptitle(tgroup[0])
                    fig.tight_layout()

                    # plt.show()
                    pdf.savefig()
                    plt.close()

        if SETTINGS_DICT['PIPELINE_SWITCHBOARD']['extract_trial_zscores']:
            # Write csv with area under curves
            with open(sep.join([trial_zscore_plots_path, file_name + '.csv']), 'w', newline='') as file:
                writer = csv.writer(file, delimiter=',')

                writer.writerow(['Recording'] + ['Trial_type'] + ['TrialID'] + ['AMDepth'] +
                                ['Trial_Onset'] + ['Trial_Offset'] + ['Area_under_curve'] + ['Peak_value'] + [
                                    'Baseline_area_under_curve'])

                for trial_type in output_dict.keys():
                    for trial_idx in range(len(output_dict[trial_type][0])):
                        # output_list[x][0] is (cur_trial['trialid'], cur_trial['amdepth'], cur_trial['trial_onset'])

                        trialID = output_dict[trial_type][0][trial_idx][0]
                        AMdepth = output_dict[trial_type][0][trial_idx][1]
                        trial_onset = output_dict[trial_type][0][trial_idx][2]
                        trial_offset = output_dict[trial_type][0][trial_idx][3]
                        writer.writerow([subj_date] + [trial_type] + [trialID] + [np.round(AMdepth, 2)] +
                                        [trial_onset] +  # Trial onset
                                        [trial_offset] +
                                        [output_dict[trial_type][1][trial_idx]] +  # Trapz
                                        [output_dict[trial_type][2][trial_idx]] +  # Peak
                                        [output_dict[trial_type][3][trial_idx]])  # Baseline AUC for dprime calculations

            with open(sep.join([trial_zscore_plots_path, file_name + '_curves.csv']), 'w', newline='') as file:
                writer = csv.writer(file, delimiter=',')

                writer.writerow(['Recording'] + ['Trial_type'] + ['Time_s'] + ['Signal_mean'] + [
                                    'Signal_SEM'])

                for trial_type in sig_mean_dict.keys():
                    for time_point_idx, _ in enumerate(sig_mean_dict[trial_type][0]):
                        writer.writerow([subj_date] +
                                        [trial_type] +
                                        [sig_mean_dict[trial_type][0][time_point_idx]] +
                                        [sig_mean_dict[trial_type][1][time_point_idx]] +
                                        [sig_mean_dict[trial_type][2][time_point_idx]])