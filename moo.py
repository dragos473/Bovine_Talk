import argparse
import pandas as pd
import parselmouth
from parselmouth.praat import call
import numpy as np
import math
import os
import gc

def detect_30_40hz_spike(sound):
    try:
        spectrum = sound.to_spectrum()
        frequencies = spectrum.xs()
        power = np.abs(spectrum.values)**2
        power = power[0]
        
        band_mask = (frequencies >= 30) & (frequencies <= 40)
        if not np.any(band_mask):
            return False
            
        band_power = power[band_mask]
        mean_power = np.mean(power)
        std_power = np.std(power)
        
        spike_threshold = mean_power + (3 * std_power)
        
        return bool(np.max(band_power) > spike_threshold)
    except Exception as e:
        print(f"        [!] Error in detect_30_40hz_spike: {e}")
        return False

def calculate_modulations_fast(obj_type, obj, tier, duration):
    try:
        num_points = call(tier, "Get number of points")
        
        if obj_type == "pitch":
            vals = obj.selected_array['frequency']
            vals = np.where(vals == 0, np.nan, vals)
        else:
            vals = obj.values[0, :]
            
    except Exception as e:
        print(f"        [!] Error extracting data from tier for {obj_type}: {e}")
        return np.nan, np.nan, np.nan
        
    infl_asc = 0
    infl_desc = 0
    variationtot = 0.0
    
    for current_frame in range(1, num_points):
        idx = current_frame - 1
        
        if idx - 1 < 0 or idx + 1 >= len(vals):
            continue
            
        val_before = vals[idx - 1]
        val_current = vals[idx]
        val_after = vals[idx + 1]
        
        if not math.isnan(val_current) and not math.isnan(val_before) and not math.isnan(val_after):
            if val_after > val_current and val_current <= val_before:
                infl_asc += 1
            elif val_after < val_current and val_current >= val_before:
                infl_desc += 1
                
            variationtot += abs(val_after - val_current)
            
    sum_infl = infl_asc + infl_desc
    var_rate = variationtot / duration if duration > 0 else 0
    mod_rate = (sum_infl / 2) / duration if duration > 0 else 0
    mod_extent = (variationtot / (sum_infl / 2)) if sum_infl > 0 else 0
    
    return var_rate, mod_rate, mod_extent

def calculate_wiener_entropy_faithful(sound, start_freq, end_freq, time_stepWE):
    if math.isnan(start_freq) or math.isnan(end_freq):
        raise ValueError("Wiener Entropy boundary frequencies evaluate to NaN")
        
    frame_duration = 0.01
    sampling_period = sound.dx
    duration = sound.get_total_duration()
    start_time = sound.xmin
    
    number_of_steps = math.floor((duration - frame_duration) / time_stepWE) + 1
    if number_of_steps <= 0:
        return np.nan
        
    sum_wiener_entropy = 0.0
    
    for _ in range(int(number_of_steps)):
        part = sound.extract_part(from_time=start_time, to_time=start_time+frame_duration, 
                                  window_shape=parselmouth.WindowShape.GAUSSIAN1, relative_width=1.0, preserve_times=True)
        start_time += time_stepWE
        
        spectrum = part.to_spectrum()
        df = spectrum.dx
        highest_freq = spectrum.xmax
        current_end_freq = min(end_freq, highest_freq)
        
        start_bin = round(start_freq / df + 1)
        end_bin = round(current_end_freq / df + 1)
        number_of_band_bins = int(end_bin - start_bin + 1)
        
        if number_of_band_bins <= 0:
            continue
            
        real_parts = spectrum.values[0, :]
        imag_parts = spectrum.values[1, :]
        powers = (real_parts / sampling_period)**2 + (imag_parts / sampling_period)**2
        
        total_bins = len(powers)
        sum_power_spectrum = 0.0
        sum_ln_power_spectrum = 0.0
        
        for b in range(start_bin, end_bin + 1):
            idx = b - 1
            if 0 <= idx < total_bins:
                p = powers[idx]
            else:
                p = 0.0
                
            sum_power_spectrum += p
            if p > 0:
                sum_ln_power_spectrum += math.log(p)
            
        arithmetic_mean = sum_power_spectrum / number_of_band_bins if number_of_band_bins > 0 else 0
        geometric_mean = math.exp(sum_ln_power_spectrum / number_of_band_bins) if number_of_band_bins > 0 else 0
        
        if arithmetic_mean > 0 and geometric_mean > 0:
            frame_wiener_entropy = math.log(geometric_mean / arithmetic_mean)
            sum_wiener_entropy += frame_wiener_entropy
            
    return sum_wiener_entropy / number_of_steps

def extract_energy_parameters(sound):
    spectrum = sound.to_spectrum()
    
    try:
        q50 = call(spectrum, "Get centre of gravity", 2)
    except Exception as e:
        raise ValueError(f"Centre of gravity (q50) evaluation failed: {e}")
        
    if math.isnan(q50):
        raise ValueError("Centre of gravity (q50) evaluates to NaN")
        
    try:
        pass_filter = spectrum.copy()
        call(pass_filter, "Filter (pass Hann band)", 0, q50, 100)
        q25 = call(pass_filter, "Get centre of gravity", 2)
        
        stop_filter = spectrum.copy()
        call(stop_filter, "Filter (stop Hann band)", 0, q50, 100)
        q75 = call(stop_filter, "Get centre of gravity", 2)
        
        smooth_spec = call(spectrum, "Cepstral smoothing", 100)
        peaks = call(smooth_spec, "To SpectrumTier (peaks)")
        table = call(peaks, "Down to Table")
        
        num_rows = call(table, "Get number of rows")
    except Exception as e:
        raise RuntimeError(f"Error during spectrum filtering or smoothing: {e}")
        
    fpeak = np.nan
    max_pow = -float('inf')
    
    for i in range(1, num_rows + 1):
        try:
            p = float(call(table, "Get value", i, "pow(dB/Hz)"))
            if p > max_pow:
                max_pow = p
                fpeak = float(call(table, "Get value", i, "freq(Hz)"))
        except (ValueError, TypeError):
            continue
            
    return q25, q50, q75, fpeak

def analyze_calf(audio_file_path):
    sound = parselmouth.Sound(audio_file_path)
    duration = sound.get_total_duration()
    
    if duration <= 0:
        raise ValueError("Audio duration evaluates to 0 or less")
    
    print("      -> Extracting Pitch...")
    pitch = call(sound, "To Pitch (cc)", 0, 70, 15, "no", 0.1, 0.2, 0.1, 0.5, 0.1, 110)
    pitch_smooth = call(pitch, "Smooth", 10)
    pitch_interp = call(pitch_smooth, "Interpolate")
    
    f0_mean = call(pitch_interp, "Get mean", 0, 0, "Hertz")
    f0_max = call(pitch_interp, "Get maximum", 0, 0, "Hertz", "Parabolic")
    f0_max_t = call(pitch_interp, "Get time of maximum", 0, 0, "Hertz", "Parabolic")
    perc_f0_max_t = (f0_max_t / duration) * 100 if duration > 0 else 0
    f0_min = call(pitch_interp, "Get minimum", 0, 0, "Hertz", "Parabolic")
    f0_abs_slope = call(pitch_interp, "Get mean absolute slope", "Hertz")
    f0_range = f0_max - f0_min
    
    print("      -> Calculating Pitch Modulations...")
    pitch_tier = call(pitch_interp, "Down to PitchTier")
    f0_var, fm_rate, fm_extent = calculate_modulations_fast("pitch", pitch_interp, pitch_tier, duration)
    
    table_voiced = call(pitch_tier, "Down to TableOfReal", "Hertz")
    nrow = call(table_voiced, "Get number of rows")
    
    try:
        f0_start = float(call(table_voiced, "Get value", 1, 2)) if nrow > 0 else np.nan
        f0_end = float(call(table_voiced, "Get value", nrow, 2)) if nrow > 0 else np.nan
    except (ValueError, TypeError):
        f0_start = np.nan
        f0_end = np.nan
    
    print("      -> Extracting Energy Parameters...")
    q25, q50, q75, fpeak = extract_energy_parameters(sound)
    
    print("      -> Extracting Intensity & Modulations...")
    intensity = call(sound, "To Intensity", 70, 0, "yes")
    intensity_tier = call(intensity, "Down to IntensityTier")
    am_var, am_rate, am_extent = calculate_modulations_fast("intensity", intensity, intensity_tier, duration)
    
    print("      -> Extracting Harmonicity...")
    harmonicity = call(sound, "To Harmonicity (cc)", 0.01, 70, 0.1, 1)
    mean_hnr = call(harmonicity, "Get mean", 0, 0)
    
    print("      -> Extracting Formants...")
    formant = call(sound, "To Formant (burg)", 0, 7, 4300, 0.01, 50)
    formant_tier = call(formant, "Down to FormantTier")
    table_f = call(formant_tier, "Down to TableOfReal", "yes", "no")
    
    f_means = []
    for i in range(1, 7):
        try:
            mean_val = call(table_f, "Get column mean (label)", f"F{i}")
            f_means.append(mean_val)
        except Exception:
            f_means.append(np.nan)
    
    df_sum = (f_means[1] - f_means[0]) + (f_means[2] - f_means[1]) + (f_means[3] - f_means[2]) + \
             (f_means[4] - f_means[3]) + (f_means[5] - f_means[4])
    df = df_sum / 5
    vtl = 35000 / (2 * df) if df != 0 and not math.isnan(df) else np.nan
    
    print("      -> Calculating Wiener Entropy...")
    we = calculate_wiener_entropy_faithful(sound, 70, q75, 0.01)
    
    return {
        "Mean F0 (Hz)": f0_mean, "Start F0 (Hz)": f0_start, "End F0 (Hz)": f0_end,
        "Max F0 (Hz)": f0_max, "Min F0 (Hz)": f0_min, "Range F0 (Hz)": f0_range,
        "Time max F0 (%)": perc_f0_max_t, "F0 Abs Slope": f0_abs_slope,
        "F0 var (Hz/s)": f0_var, "FM Rate (s-1)": fm_rate, "FM Extent (Hz)": fm_extent,
        "Q25% (Hz)": q25, "Q50% (Hz)": q50, "Q75% (Hz)": q75, "Fpeak (Hz)": fpeak,
        "Sound duration (s)": duration, "AM var (dB/s)": am_var, "AM rate (s-1)": am_rate,
        "AM extent (dB)": am_extent, "Harmonicity": mean_hnr,
        "F1 mean (Hz)": f_means[0], "F2 mean (Hz)": f_means[1], "F3 mean (Hz)": f_means[2],
        "F4 mean (Hz)": f_means[3], "F5 mean (Hz)": f_means[4], "F6 mean (Hz)": f_means[5],
        "formant dispersal (Hz)": df, "vocal tract length (cm)": vtl, "mean wiener entropy": we
    }

def analyze_cow(audio_file_path, call_type="LFC"):
    sound = parselmouth.Sound(audio_file_path)
    duration = sound.get_total_duration()
    
    if duration <= 0:
        raise ValueError("Audio duration evaluates to 0 or less")
    
    if call_type == "LFC":
        time_step = 0.01; min_F0 = 60; max_F0 = 120
        max_nb_cand = 15; sil_threshold = 0.15; voic_threshold = 0.15
        oct_cost = 0.1; oct_jump_cost = 0.7; voic_unvoic_cost = 0.14
        time_step_f = 0.01; max_num_formants = 9; max_formant = 4000
        window_length = 0.01; pre_emphasis = 50
    else:
        time_step = 0.01; min_F0 = 60; max_F0 = 300
        max_nb_cand = 15; sil_threshold = 0.15; voic_threshold = 0.15
        oct_cost = 0.1; oct_jump_cost = 0.7; voic_unvoic_cost = 0.14
        time_step_f = 0.01; max_num_formants = 9; max_formant = 3500
        window_length = 0.01; pre_emphasis = 50
        
    print("      -> Extracting Pitch...")
    pitch = call(sound, "To Pitch (cc)", time_step, min_F0, max_nb_cand, "no", 
                 sil_threshold, voic_threshold, oct_cost, oct_jump_cost, voic_unvoic_cost, max_F0)
    pitch_smooth = call(pitch, "Smooth", 10)
    pitch_interp = call(pitch_smooth, "Interpolate")
    
    f0_mean = call(pitch_interp, "Get mean", 0, 0, "Hertz")
    f0_max = call(pitch_interp, "Get maximum", 0, 0, "Hertz", "Parabolic")
    f0_min = call(pitch_interp, "Get minimum", 0, 0, "Hertz", "Parabolic")
    f0_range = f0_max - f0_min
    
    print("      -> Extracting Energy Parameters...")
    q25, q50, q75, fpeak = extract_energy_parameters(sound)
    
    print("      -> Extracting Intensity & Modulations...")
    intensity = call(sound, "To Intensity", min_F0, time_step, "yes")
    intensity_tier = call(intensity, "Down to IntensityTier")
    am_var, am_rate, am_extent = calculate_modulations_fast("intensity", intensity, intensity_tier, duration)
    
    print("      -> Extracting Harmonicity...")
    harmonicity = call(sound, "To Harmonicity (cc)", time_step, min_F0, sil_threshold, 1)
    mean_hnr = call(harmonicity, "Get mean", 0, 0)
    
    print("      -> Extracting Formants...")
    formant = call(sound, "To Formant (burg)", time_step_f, max_num_formants, max_formant, window_length, pre_emphasis)
    formant_tier = call(formant, "Down to FormantTier")
    table_f = call(formant_tier, "Down to TableOfReal", "yes", "no")
    
    f_means = []
    for i in range(1, 9):
        try:
            mean_val = call(table_f, "Get column mean (label)", f"F{i}")
            f_means.append(mean_val)
        except Exception:
            f_means.append(np.nan)
    
    df_sum = (f_means[1] - f_means[0]) + (f_means[2] - f_means[1]) + (f_means[3] - f_means[2]) + \
             (f_means[4] - f_means[3]) + (f_means[5] - f_means[4]) + (f_means[6] - f_means[5]) + \
             (f_means[7] - f_means[6])
    df = df_sum / 7
    vtl = 35000 / (2 * df) if df != 0 and not math.isnan(df) else np.nan
    
    print("      -> Calculating Wiener Entropy...")
    we = calculate_wiener_entropy_faithful(sound, 50, q75, 0.004)
    
    return {
        "Call type": call_type, "Mean F0": f0_mean, "Max F0": f0_max, 
        "Min F0": f0_min, "Range F0": f0_range, "Q25%": q25, "Q50%": q50, 
        "Q75%": q75, "Fpeak": fpeak, "sound duration": duration, 
        "AM var": am_var, "AM rate": am_rate, "AM extent": am_extent, 
        "harmonicity": mean_hnr, "F1 mean": f_means[0], "F2 mean": f_means[1], 
        "F3 mean": f_means[2], "F4 mean": f_means[3], "F5 mean": f_means[4], 
        "F6 mean": f_means[5], "F7 mean": f_means[6], "F8 mean": f_means[7], 
        "formant dispersal": df, "vocal tract length": vtl, "mean wiener entropy": we
    }

def process_directory(input_dir, output_csv, animal_type, call_type="LFC"):
    supported_extensions = ('.wav', '.aif', '.aiff', '.au')
    results = []
    
    if not os.path.isdir(input_dir):
        print(f"Error: Directory '{input_dir}' not found.")
        return
        
    all_files = os.listdir(input_dir)
    supported_files = [f for f in all_files if f.lower().endswith(supported_extensions)]
    total_files = len(supported_files)
    
    if total_files == 0:
        print("No supported audio files found in the directory.")
        return
        
    print(f"Found {total_files} supported audio files. Starting processing...")
        
    for index, filename in enumerate(supported_files, start=1):
        print(f"[{index}/{total_files}] Processing {filename}...")
        
        try:
            file_path = os.path.join(input_dir, filename)
            sound = parselmouth.Sound(file_path)
            
            row_data = {"file": filename}
            
            if animal_type == 'calf':
                metrics = analyze_calf(file_path)
            elif animal_type == 'cow':
                metrics = analyze_cow(file_path, call_type)
            else:
                metrics = {}
                
            for key, val in metrics.items():
                if key == "FM Extent (Hz)":
                    row_data[key] = val
                elif isinstance(val, (float, int)) and not (isinstance(val, float) and math.isnan(val)):
                    row_data[key] = f"{val:.3f}"
                else:
                    row_data[key] = val
            
            row_data["Comment"] = ""
            print("      -> Verifying 30-40Hz unvoicing spike...")
            row_data['Unvoicing_Required_Flag'] = detect_30_40hz_spike(sound)
                
            results.append(row_data)
            print(f"    -> Successfully processed {filename}")
            
        except Exception as e:
            print(f"    -> Skipped {filename} (Reason: {e})")
        finally:
            gc.collect()
            
    if results:
        df = pd.DataFrame(results)
        df.to_csv(output_csv, index=False)
        print(f"\nFinished! Processed {len(results)} files. Output written to '{output_csv}'")
    else:
        print("\nAll files were skipped. No output generated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acoustic analysis for bovine vocalizations in a directory.")
    
    parser.add_argument("-i", "--input_dir", required=True, help="Directory containing the audio files.")
    parser.add_argument("-o", "--output", required=True, help="Path to the output CSV file.")
    parser.add_argument("-a", "--animal", required=True, choices=["calf", "cow"], help="Animal type for the dataset.")
    parser.add_argument("-c", "--call_type", choices=["LFC", "HFC"], default="LFC", help="Call type (cow only). Default is LFC.")
    
    args = parser.parse_args()
    
    process_directory(args.input_dir, args.output, args.animal, args.call_type)