#!/usr/bin/env python
# coding: utf-8

"""
Python driver for the Nanonis SPM (scanning probe microscopy) control system
over TCP/IP, communicating through pyvisa message-based sockets.

The module implements the Nanonis binary protocol and exposes high-level methods
for tip/Z control, XY positioning, scanning, signal acquisition, bias and
analog-output ramping, the generic sweeper, PLL/lock-in configuration, coarse
motor movement, and oscilloscope readout.

Several methods -- notably auto-approach, safe-tip, and the scan Action
handling -- are adapted from the open-source NanonisTCP package.
"""

import pyvisa
import struct
import numpy as np
import time
import os
import re



class Nanonis():
    """TCP/IP driver for a Nanonis SPM controller.

    Constructed with a pyvisa resource manager, which opens a TCP/IP session to
    the Nanonis hardware.
    """

    def __init__(self, resource_manager, resource_name: str):
        """Open the TCP/IP session and cache the session path and RT signal names."""
        try:
            self.instr: pyvisa.resources.MessageBasedResource = resource_manager.open_resource(resource_name)
            self.resource_manager = resource_manager
            self.instr.write_termination = ''
            self.instr.set_visa_attribute(pyvisa.constants.VI_ATTR_SUPPRESS_END_EN, pyvisa.constants.VI_FALSE)
            self.instr.timeout = 3600000

            # Query session folder path and update class attributes
            self.session: str = self.session_path_get()

            # Tip configuration should be implemented for each type individually

            # Create signals list. Signal RT index can be retrieved by the index in this list
            self.signals: list = self.sig_names_get()
        except Exception as e:
            self.instr.close()
            print(e)

    # ---------------------------------------------------------------------------
    # Communication
    # ---------------------------------------------------------------------------
    # Refer to the Nanonis TCP/IP protocol for the request/response format.

    def request_header_encode(self, command_name: str, body_size: int, send_back=1):
        """Encode a Nanonis request header (string form)."""
        cmd_header = command_name.ljust(32, '\x00')
        cmd_header += struct.pack('>i', body_size).decode() + struct.pack('>H', send_back).decode() + '\x00\x00'
        return cmd_header

    def request_header_encode_raw(self, command_name: bytes, body_size: int, send_back=1):
        """Encode a Nanonis request header (raw bytes form)."""
        cmd_header = command_name.ljust(32, b'\x00')
        cmd_header += struct.pack('>i', body_size) + struct.pack('>H', send_back) + b'\x00\x00'
        return cmd_header

    def resp_body_size(self, response: str):
        """Decode the body size from a full Nanonis response message."""
        # 'response' is expected to be the whole response message.
        body_size_bytes = int(response[32:36].hex(), 16)
        return body_size_bytes

    def resp_error_decode(self, error_message: str):
        """Decode a Nanonis error trailer into (error_status, error_description)."""
        # 'error_message' is not the whole response, but the error trailer that
        # follows the return values.
        error_status = struct.unpack('!I', error_message[0:4])[0]
        error_size = struct.unpack('!i', error_message[4:8])[0]
        error_descr = error_message[8:8 + error_size].decode('UTF-8')
        return error_status, error_descr

    def write(self, command: str):
        """Write a string command to Nanonis."""
        self.instr.write(command)

    def write_raw(self, command: bytes):
        """Write a raw bytes command to Nanonis."""
        self.instr.write_raw(command)

    def read(self):
        """Read a raw bytes response from Nanonis."""
        return self.instr.read_raw()

    def string_array_to_list(self, resp: str, ele_num: int):
        """Decode a Nanonis string array into a Python list of strings."""
        # Refer to the Nanonis TCP/IP protocol for the string array format.
        arr = []
        ele_start = 0
        for i in range(ele_num):
            # Each Nanonis string array element starts with its length.
            len = struct.unpack('!i', resp[ele_start: 4 + ele_start])[0]
            arr.append(resp[4 + ele_start:4 + ele_start + len].decode('utf-8'))
            ele_start += 4 + len
        return arr

    def data_array_to_tuple(self, resp, ele_num, data_type: str):
        """Decode a Nanonis int/float array into a tuple.

        Use this for numbers whose type should not change, e.g. signal indexes.
        """
        # data_type codes follow Python's 'struct' module. Examples:
        # int32 = 'i', unsigned int16 = 'H', unsigned int32 = 'I'
        # float32 = 'f', float64 = 'd'
        t = '!' + data_type * ele_num
        tuple_data = struct.unpack(t, resp)
        return tuple_data

    def data_array_to_np(self, resp, ele_num, data_type: str):
        """Decode a Nanonis 1-D int/float array into a numpy ndarray (use for data)."""
        # data_type codes follow Python's 'struct' module. Examples:
        # int32 = 'i', unsigned int16 = 'H', unsigned int32 = 'I'
        # float32 = 'f', float64 = 'd'
        t = '!' + data_type * ele_num
        tuple_data = struct.unpack(t, resp)
        return np.array(tuple_data)

    def data_2d_array_to_np(self, resp, rows, cols, data_type: str):
        """Decode a Nanonis 2-D int/float array into a numpy ndarray (use for data)."""
        # data_type codes follow Python's 'struct' module. Examples:
        # int32 = 'i', unsigned int16 = 'H', unsigned int32 = 'I'
        # float32 = 'f', float64 = 'd'
        t = '!' + data_type * cols
        ele_size = len(resp) / (rows * cols)
        data_array = []
        for j in range(rows):
            data_row = struct.unpack(t, resp[j * cols * ele_size:(j + 1) * cols * ele_size])
            data_array.append(data_row)
        return np.array(data_array)

    def list_to_data_array(self, list_c, data_type: str):
        """Encode an int/float list/tuple into a Nanonis byte array for command payloads."""
        data_array = ''
        pack = '>' + data_type
        for data in list_c:
            data_array += struct.pack(pack, data)
        return data_array

    # ---------------------------------------------------------------------------
    # Auto-Approach
    # ---------------------------------------------------------------------------
    # Adapted from the NanonisTCP package.
    def autoApproach_Open(self):
        """
        Opens the Auto Approach module

        """
        command = self.request_header_encode_raw(b'AutoApproach.Open', body_size=0, send_back=0)
        self.write_raw(command)

    def autoApproach_OnOffSet(self, on_off):
        """
        Starts or stops the Z auto-approach procedure

        Parameters
        ----------
        on_off : starts or stops Z auto-approach
                 False: Stops it
                 True:  Starts it

        """
        command = self.request_header_encode_raw(b'AutoApproach.OnOffSet', body_size=2, send_back=0)
        command += struct.pack('>H', on_off)
        self.write_raw(command)

    def autoApproach_OnOffGet(self):
        """
        Returns the on-off status of the Z auto-approach procedure

        Returns
        -------
        on_off : starts or stops Z auto approach
                 False: Off
                 True:  Running

        """
        command = self.request_header_encode_raw(b'AutoApproach.OnOffGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        on_off = struct.unpack('!H', resp[40:42])[0]

        return on_off

    # ---------------------------------------------------------------------------
    # Safe Tip
    # ---------------------------------------------------------------------------
    # Adapted from the NanonisTCP package.
    def safeTip_OnOffSet(self, safe_tip_status):
        """
        Switches the Safe Tip feature on or off

        Parameters
        ----------
        safe_tip_status : sets the Safe Tip feature on or off
                          0: No change (leave setting as is in nanonis)
                          1: Safe Tip feature on
                          2: Safe Tip feature off

        """
        command = self.request_header_encode_raw(b'SafeTip.OnOffSet', body_size=2, send_back=0)
        command += struct.pack('>H', safe_tip_status)
        self.write_raw(command)

    def safeTip_OnOffGet(self):
        """
        Returns the Safe Tip feature status (on/off)

        Returns
        -------
        safe_tip_status : sets the Safe Tip feature on or off
                          True:  Safe Tip feature on
                          False: Safe Tip feature off

        """
        command = self.request_header_encode_raw(b'SafeTip.OnOffGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        on_off = struct.unpack('!H', resp[40:42])[0]

        return on_off

    # ---------------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------------
    def session_path_get(self):
        """Query the Nanonis session folder path and cache it on the instance."""
        command = self.request_header_encode('Util.SessionPathGet', body_size=0)
        self.write(command)
        resp = self.read()
        session_path_size = struct.unpack('!i', resp[40:44])[0]
        session_path: str = resp[44:44 + session_path_size].decode('UTF-8')
        self.session = session_path
        return session_path

    def acq_period_get(self):
        """Return the Nanonis signal acquisition period (s)."""
        command = self.request_header_encode_raw(b'Util.AcqPeriodGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        acq_period = struct.unpack('!f', resp[40:44])[0]
        return acq_period

    def extract_counter(self, basename):
        """Return the next available .dat file counter for the given basename."""
        pattern = re.compile(rf'^{re.escape(basename)}(\d+)\.dat$')
        counter = -1
        for file in os.listdir(self.session):
            check = pattern.match(file)
            if check:
                file_num = check.group(1)
                if file_num > counter:
                    counter = file_num
        return (counter + 1)
        # This is the counter of the file 'to be created'

    def new_file_name(self, basename):
        """Build a new .dat file name for the given basename using the file counter."""
        try:
            counter = self.extract_counter(basename)
            new_name = basename + str(counter + 1) + '.dat'
            if os.path.exists(self.session + '\\' + new_name):
                raise FileExistsError('file name already exists')
        except:
            return
        else:
            return new_name

    # ---------------------------------------------------------------------------
    # PLL
    # ---------------------------------------------------------------------------
    def PLL_out_onoff_set(self, modulator_index: int, state: int):
        """
        Enable or disable PLL output for the given modulator index.
        state: 0 = off, 1 = on
        """
        command_name = b'PLL.OutOnOffSet'
        body_size = 8  # 4 (index) + 4 (state)
        header = self.request_header_encode_raw(command_name, body_size, send_back=1)
        command = header + struct.pack('>i', modulator_index) + struct.pack('>I', state)
        self.write_raw(command)
        self.read()  # Read to clear response buffer (even if unused)

    def PLL_out_onoff_get(self, modulator_index: int) -> int:
        """
        Query PLL output enable status (0 or 1).
        """
        command_name = b'PLL.OutOnOffGet'
        body_size = 4  # Only modulator index
        header = self.request_header_encode_raw(command_name, body_size, send_back=1)
        command = header + struct.pack('>i', modulator_index)
        self.write_raw(command)
        response = self.read()
        return struct.unpack('>I', response[40:44])[0]

    def PLL_phase_ctrl_onoff_set(self, modulator_index: int, state: int):
        """
        Enable or disable PLL phase control for the given modulator index.
        state: 0 = off, 1 = on
        """
        command_name = b'PLL.PhasCtrlOnOffSet'
        body_size = 8
        header = self.request_header_encode_raw(command_name, body_size, send_back=1)
        command = header + struct.pack('>i', modulator_index) + struct.pack('>I', state)
        self.write_raw(command)
        self.read()  # Read to clear response buffer

    def PLL_phase_ctrl_onoff_get(self, modulator_index: int) -> int:
        """
        Query PLL phase control enable status (0 or 1).
        """
        command_name = b'PLL.PhasCtrlOnOffGet'
        body_size = 4
        header = self.request_header_encode_raw(command_name, body_size, send_back=1)
        command = header + struct.pack('>i', modulator_index)
        self.write_raw(command)
        response = self.read()
        return struct.unpack('>I', response[40:44])[0]

    def PLL_freq_shift_auto_center(self, modulator_index: int):
        """Auto-center the PLL frequency shift for the given modulator index."""
        command_name = b'PLL.FreqShiftAutoCenter'
        body_size = 4
        header = self.request_header_encode_raw(command_name, body_size, send_back=1)
        command = header + struct.pack('>i', modulator_index)
        self.write_raw(command)
        self.read()  # Read to clear response buffer

    # ---------------------------------------------------------------------------
    # Signals
    # ---------------------------------------------------------------------------
    # RT signals are accessed by their signal index (0-127). The helper methods
    # below convert between the channel names shown in the Nanonis UI and these
    # indexes. Note: the current Nanonis TCP/IP protocol no longer uses in-slot
    # settings.

    def sig_names_get(self):
        """Return the list of 128 RT signal names, ordered by signal index 0-127."""
        command = self.request_header_encode_raw(b'Signals.NamesGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        sig_name_size, sig_num = struct.unpack('!ii', resp[40:48])
        sig_names = self.string_array_to_list(resp[48:48 + sig_name_size], sig_num)
        return sig_names

    def sig_meas_names_get(self):
        """Return the measurement channel names (used by the Generic Sweeper)."""
        command = self.request_header_encode('Signals.MeasNamesGet', body_size=0)
        self.write(command)
        resp = self.read()
        list_size, num = struct.unpack('!ii', resp[40:48])
        chn_names = self.string_array_to_list(resp[48:48 + list_size], num)
        print(chn_names)
        return chn_names

    def sig_index_get(self, *chn_names):
        """Return the RT signal indexes for the given channel names.

        Args:
            *chn_names: One or more signal channel names.

        Returns:
            list[int]: RT signal index for each channel, in the input order.
        """
        chn_index = []
        for name in chn_names:
            chn_index.append(self.signals.index(name))
        return chn_index

    def sig_val_get_by_index(self, index, wait_new=1):
        """Return a single signal value (float) for the given RT signal index.

        Args:
            index: RT signal index (0-127).
            wait_new: 1 waits for the newest data, 0 returns the current value.
        """
        command = self.request_header_encode_raw(b'Signals.ValGet', body_size=8)
        command += struct.pack('>i', index) + struct.pack('>I', wait_new)
        self.write_raw(command)
        resp = self.read()
        sig_val = struct.unpack('!f', resp[40:44])[0]
        return sig_val

    def sig_vals_get_by_index(self, *indices, wait_new=1):
        """Return multiple signal values for the given RT signal indexes.

        Args:
            *indices: RT signal indexes given directly or as a single list/tuple.
            wait_new: 1 waits for the newest data, 0 returns the current values.

        Returns:
            np.ndarray: Signal values (float32), one per index.
        """
        if len(indices) == 1 and isinstance(indices[0], (list, tuple)):
            index_list = list(indices[0])
        else:
            index_list = list(indices)
        index_size = 4 * len(index_list)  # size of index_list in bytes
        index_command = bytes(index_list)
        command = self.request_header_encode_raw(b'Signals.ValsGet',
                                                 body_size=4 + index_size + 4)  # first 4 for signals index size, second 4 for 'wait for newest data'
        command += struct.pack('>i', index_size) + index_command + struct.pack('>I', wait_new)
        self.write_raw(command)
        resp = self.read()
        sig_vals_size = struct.unpack('!i', resp[40:44])[0]
        sig_vals = self.data_array_to_np(resp[44:44 + sig_vals_size], len(index_list), 'f')
        return sig_vals

    def sig_vals_get(self, *chn_names):
        """Return signal values for the given channel names (waits for newest data).

        Example:
            sig_vals_get("Input 3 (V)", "Current (A)")
        """
        index_list = self.sig_index_get(*chn_names)
        vals = self.sig_vals_get_by_index(index_list)
        return vals  # wait for new data is always set to be True here

    def sig_avg_vals_get(self, avg_period, *chn_names):
        """Return signal values averaged over avg_period (s) for the given channels.

        Args:
            avg_period: Averaging window in seconds.
            *chn_names: One or more signal channel names.
        """
        tap = self.acq_period_get()
        avg_times = int(avg_period / tap)
        sig_avg = np.zeros(len(chn_names))
        index_list = self.sig_index_get(*chn_names)
        for i in range(avg_times):
            if i == 0:
                sig_avg += self.sig_vals_get_by_index(index_list, wait_new=1)
            else:
                sig_avg += self.sig_vals_get_by_index(index_list,
                                                      wait_new=0)  # Wait for new data for the first measurement.
        sig_avg = sig_avg / avg_times
        return sig_avg

    # ---------------------------------------------------------------------------
    # Bias
    # ---------------------------------------------------------------------------
    def bias_volt_set(self, val):
        """Set the bias voltage to val (V)."""
        command = self.request_header_encode_raw(b'Bias.Set', body_size=4, send_back=0)
        command += struct.pack('>f', val)
        self.write_raw(command)
        return val

    def bias_volt_get(self):
        """Return the current bias voltage (V)."""
        command = self.request_header_encode_raw(b'Bias.Get', body_size=0)
        self.write_raw(command)
        resp = self.read()
        val = struct.unpack('!f', resp[40:44])[0]
        return val

    def bias_volt_ramp(self, target, ramp_rate, start, max_rate=1, step_size=0.001):
        """Ramp the bias voltage from start to target (V) at ramp_rate (V/s).

        Args:
            target: Target bias voltage (V).
            ramp_rate: Ramp rate (V/s); must not exceed max_rate.
            start: Starting bias voltage (V).
            max_rate: Maximum allowed ramp rate (V/s); must be < 2 V/s.
            step_size: Nominal voltage step size (V).
        """
        if max_rate >= 2:
            print(f'Maximum rate is too large. Please choose a maximum ramp rate < 2 V/s')
        else:
            if ramp_rate > max_rate:
                print(f'Ramp rate is too large. Please choose a ramp rate < {max_rate} V/s')
            else:
                # The largest possible volt stepsize would be 0.001 * max_rate V
                steps = max(int((np.abs(target - start) / ramp_rate) / 0.001), 10)  # steps is at least 10
                step_wait = (np.abs(target - start) / ramp_rate) / steps
                volt_arr = np.linspace(start, target, steps)
                for volt in volt_arr:
                    self.bias_volt_set(volt)
                    time.sleep(step_wait)  # Note that time.sleep(t) may not be pausing exactly t seconds

    # ---------------------------------------------------------------------------
    # Analog Output
    # ---------------------------------------------------------------------------
    def AO_volt_set_by_index(self, chn_index, val):
        """Set analog output channel chn_index to val (V).

        The index matches the 'Output XX' number, e.g. Output 8 is index 8.
        """
        command = self.request_header_encode_raw(b'UserOut.ValSet', body_size=8, send_back=0)
        command += struct.pack('>i', chn_index) + struct.pack('>f', val)
        self.write_raw(command)
        return val

    def AO_volt_set(self, channel, val):
        """Set analog output to val (V), selecting the channel by name (e.g. 'Output 8 (V)')."""
        chn_index = int(channel[7])
        self.AO_volt_set_by_index(chn_index, val)
        return val

    # Analog-input voltages are read via the Signals value-get methods; no
    # dedicated analog-input getter is needed.

    def AO_volt_ramp(self, channel, target, ramp_rate, max_rate=10):
        """Ramp an analog output channel to target (V) at ramp_rate (V/s).

        Args:
            channel: Output channel name (e.g. 'Output 3 (V)').
            target: Target voltage (V).
            ramp_rate: Ramp rate (V/s); must be < max_rate.
            max_rate: Maximum allowed ramp rate (V/s).
        """
        if ramp_rate < max_rate:
            start = self.sig_vals_get(channel)[0]
            if np.abs(target - start) < 0.01:
                self.AO_volt_set(channel, target)
            else:
                steps = int((np.abs(target - start) / ramp_rate) / 0.001) + 1
                volt_arr = np.linspace(start, target, steps)
                for i in range(steps):
                    self.AO_volt_set(channel, volt_arr[i])
                    time.sleep(0.001)
        else:
            print(f"ramp rate is too large. Please choose a ramp rate < {max_rate} V/s")

    def AO_volt_ramp_2ch(self, channel_1, channel_2, target_1, diff, ramp_rate, max_rate=10):
        """Ramp two analog output channels together at ramp_rate (V/s), holding a fixed offset.

        The channels are kept at a constant difference diff = channel_2 - channel_1
        (V). If they do not already differ by diff, channel_2 is first ramped to
        channel_1 + diff; both channels are then ramped simultaneously to target_1
        and target_1 + diff.

        Args:
            channel_1: First output channel name.
            channel_2: Second output channel name.
            target_1: Target voltage for channel_1 (V).
            diff: Fixed offset channel_2 - channel_1 (V).
            ramp_rate: Ramp rate (V/s).
            max_rate: Maximum allowed ramp rate (V/s).
        """
        start_1, start_2 = self.sig_vals_get(channel_1, channel_2)
        if start_2 - start_1 != diff:
            self.AO_volt_ramp(channel_2, start_1 + diff, ramp_rate, max_rate)
        else:
            steps = int((np.abs(target_1 - start_1) / ramp_rate) / 0.001) + 1
            volt_1 = np.linspace(start_1, target_1, steps)
            volt_2 = volt_1 + diff
            for i in range(steps):
                self.AO_volt_set(channel_2, volt_2[i])
                self.AO_volt_set(channel_1, volt_1[i])
                time.sleep(0.03)

    def AO_bias_ramp_2ch(self, AO_channel, AO_target, diff, ramp_rate, max_rate=10):
        """Ramp an analog output channel and the bias together, holding a fixed offset.

        The bias is kept at diff = bias - AO_channel (V). If they do not already
        differ by diff, the bias is first ramped to AO_start + diff; both are then
        ramped simultaneously.

        Args:
            AO_channel: Output channel name.
            AO_target: Target voltage for the output channel (V).
            diff: Fixed offset bias - AO_channel (V).
            ramp_rate: Ramp rate (V/s).
            max_rate: Maximum allowed ramp rate (V/s).
        """
        AO_start, bias_start = self.sig_vals_get(AO_channel, 'Bias (V)')
        if bias_start - AO_start != diff:
            self.bias_volt_ramp(AO_start + diff, ramp_rate, max_rate)
        else:
            steps = int((np.abs(AO_target - AO_start) / ramp_rate) / 0.001) + 1
            AO_volt = np.linspace(AO_start, AO_target, steps)
            bias_volt = AO_volt + diff
            for i in range(steps):
                self.bias_volt_set(bias_volt[i])
                self.AO_volt_set(AO_channel, AO_volt[i])
                time.sleep(0.03)

    # ---------------------------------------------------------------------------
    # Generic Sweeper
    # ---------------------------------------------------------------------------
    def GS_acq_chn_set(self, *chn_names):
        """Set the Generic Sweeper acquisition channels by name."""
        # Validate the channel names first; if any is invalid, print the available
        # channels as an error message.
        try:
            index_list = self.sig_index_get(*chn_names)
        except:
            print('Unable to find channel. See below the available channels:\n')
            print(self.sig_meas_names_get())
        # Otherwise, set the acquisition channel names.
        else:
            command = self.request_header_encode_raw(b'GenSwp.AcqChsSet', body_size=4 + 4 * len(index_list),
                                                     send_back=0)
            index_command = self.list_to_data_array(index_list, 'i')
            command += struct.pack('>i', len(index_list)) + index_command
            self.write_raw(command)
            print('Set acquiring signals to ', chn_names)

    def GS_acq_chn_get(self):
        """Return the Generic Sweeper acquisition channels as (indexes, names)."""
        command = self.request_header_encode_raw(b'GenSwp.AcqChsGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        chn_num = struct.unpack('!i', resp[40:44])[0]
        chn_index = self.data_array_to_tuple(resp[44:44 + 4 * chn_num], chn_num, 'i')
        chn = []
        for j in chn_index:
            chn.append(self.signals[j])
        print('Now acquiring signals: ', chn)
        return chn_index, chn

    def GS_sweep_sig_set(self, chn_name: str):
        """Set the Generic Sweeper sweep signal by name (only one channel is swept).

        Example:
            GS_sweep_sig_set("Output 3 (V)")
        """
        chn_name_b = bytes(chn_name, 'ascii')
        name_size = len(chn_name_b)
        command = self.request_header_encode_raw(b'GenSwp.SwpSignalSet', body_size=4 + name_size, send_back=0)
        command += struct.pack('>i', name_size) + chn_name_b
        self.write_raw(command)
        print('set sweep signal as ', chn_name)

    def GS_sweep_sig_get(self):
        """Print the currently selected Generic Sweeper sweep signal name."""
        command = self.request_header_encode_raw(b'GenSwp.SwpSignalGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        name_size = struct.unpack('!i', resp[40:44])[0]
        chn_name = resp[44:44 + name_size].decode('utf-8')
        print('Now sweeping ', chn_name)

    def GS_limits_set(self, lower_lim, upper_lim):
        """Set the Generic Sweeper start/end limits (in the sweep signal's default units)."""
        command = self.request_header_encode_raw(b'GenSwp.LimitsSet', body_size=8, send_back=0)
        command += struct.pack('>f', lower_lim) + struct.pack('>f', upper_lim)
        self.write_raw(command)
        print(f'set lower/upper limit to {lower_lim} / {upper_lim}')

    def GS_limits_get(self):
        """Return the current Generic Sweeper (lower_lim, upper_lim)."""
        command = self.request_header_encode_raw(b'GenSwp.LimitsGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        lower_lim, upper_lim = struct.unpack('!ff', resp[40:48])
        print(f'The lower/upper sweep limit: {lower_lim} / {upper_lim}')
        return lower_lim, upper_lim

    def GS_prop_set(self, *, ini_settling_time=30, max_slew_rate=1,
                    step=0, period=0, autosave=-1, save_dialog=-1, settling_time=30):
        """Set the Generic Sweeper properties (keyword-only arguments).

        Args:
            ini_settling_time: Wait time before starting the sweep (ms).
            max_slew_rate: Maximum sweep rate (units/s).
            step: Number of steps; 0 means no change.
            period: Sweep period (ms); 0 means no change.
            autosave: Autosave sweep data: -1 no change, 0 off, 1 on.
            save_dialog: Show the save dialog: -1 no change, 0 off, 1 on.
            settling_time: Settling time at each point during the sweep (ms).
        """
        command = self.request_header_encode_raw(b'GenSwp.PropsSet', body_size=26, send_back=0)
        command += struct.pack('>f', ini_settling_time) + struct.pack('>f', max_slew_rate) + struct.pack('>i',
                                                                                                         step) + struct.pack(
            '>H', period) + struct.pack('>i', autosave) + struct.pack('>i', save_dialog) + struct.pack('>f',
                                                                                                       settling_time)
        self.write_raw(command)

    def GS_start(self, basename, get_data=0, sweep_direction=1, reset_signal=1):
        """Start a Generic Sweeper sweep.

        Args:
            basename: Save basename; empty string keeps the current basename.
            get_data: 1 returns sweep data to the read buffer, 0 does not.
            sweep_direction: 1 sweeps from the lower limit, 0 from the upper limit.
            reset_signal: 1 on, 0 off.
        """
        basename_b = bytes(basename, 'ascii')
        name_size = len(basename_b)
        command = self.request_header_encode_raw(b'GenSwp.Start', body_size=16 + name_size, send_back=1)
        command += struct.pack('>I', get_data) + struct.pack('>I', sweep_direction) + struct.pack('>i',
                                                                                                  name_size) + basename_b + struct.pack(
            '>I', reset_signal)
        self.write_raw(command)
        resp = self.read()

    def GS_stop(self):
        """Stop the Generic Sweeper sweep.

        Whether the data is saved depends on the sweeper's 'autosave' property.
        """
        command = self.request_header_encode_raw(b'GenSwp.Stop', body_size=0, send_back=0)
        self.write_raw(command)

    def GS_2d_sweep(self, basename, slow_chn_name, slow_start, slow_end, slow_pt, slow_rate):
        """Perform a 2-D sweep: ramp a slow channel and trigger a fast sweep at each step.

        The fast (sweep) channel must be configured on the Generic Sweeper front
        panel, with autosave on and the save dialog off.

        Args:
            basename: Save basename (need not match the sweeper front panel).
            slow_chn_name: Slow channel name, e.g. "Output 2 (V)".
            slow_start: Slow channel start value.
            slow_end: Slow channel end value.
            slow_pt: Number of slow-channel setpoints.
            slow_rate: Ramp rate of the slow channel between points.
        """
        slow_array = np.linspace(slow_start, slow_end, slow_pt)
        for j in range(slow_pt):
            self.AO_volt_ramp(slow_chn_name, slow_array[j], slow_rate)
            self.GS_start(basename)
        self.AO_volt_ramp(slow_chn_name, 0, slow_rate)

    def GS_z_sweep(self, basename, z_start, z_end, z_pt, z_ramp_time):
        """Sweep versus z: ramp z and trigger a sweep at each z (e.g. capacitance-z).

        Args:
            basename: Save basename.
            z_start: Start z position (m).
            z_end: End z position (m).
            z_pt: Number of z setpoints.
            z_ramp_time: Ramp time between successive z setpoints (s).
        """
        self.z_ramp(z_start, 2)
        z_array = np.linspace(z_start, z_end, z_pt)
        for j in range(z_pt):
            self.z_ramp(z_array[j], z_ramp_time)
            self.GS_start(basename)
        self.z_ramp(0, 2)

    # ---------------------------------------------------------------------------
    # Digital lines
    # ---------------------------------------------------------------------------
    def OutStatusSet(self, port, line, status):
        """Set a digital output line status.

        Args:
            port: Digital port (e.g. Port B = 1).
            line: Line number within the port (e.g. line 1 = 1, line 2 = 2).
            status: 1 active, 0 inactive.
        """
        command = self.request_header_encode_raw(b'DigLines.OutStatusSet', body_size=12, send_back=0)
        command += struct.pack('>I', port)
        command += struct.pack('>I', line)
        command += struct.pack('>I', status)
        self.write_raw(command)

    def TTLValGet(self, port, line):
        """Read the TTL value of a digital line.

        Args:
            port: Digital port (e.g. Port A = 0).
            line: Line number within the port (e.g. line 3 = 3).
        """
        command = self.request_header_encode_raw(b'DigLines.TTLValGet', body_size=2, send_back=1)
        command += struct.pack('<H', port)
        self.write_raw(command)
        resp = self.read()
        voltages_size = struct.unpack('<I', resp[40:44])[0]
        voltages_line = struct.unpack('<I', resp[40 + line * 4:44 + line * 4])

        return voltages_size, voltages_line

    # ---------------------------------------------------------------------------
    # Z-position
    # ---------------------------------------------------------------------------
    def z_pos_set(self, z_pos):
        """Set the z-position (m); must be negative and within +/-1e-5 m."""
        if np.abs(z_pos) > 1e-5:
            # z is not in um. The value should be within e-6
            print('Unit error. Z Position unit is meter')
            return
        elif z_pos > 0:
            # z is always negative to ensure piezo is extending
            print('z is negative')
            return
        else:
            command = self.request_header_encode_raw(b'ZCtrl.ZPosSet', body_size=4, send_back=0)
            command += struct.pack('>f', z_pos)
            self.write_raw(command)
            return z_pos

    def z_pos_get(self):
        """Return the current z-position (m)."""
        command = self.request_header_encode_raw(b'ZCtrl.ZPosGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        z_pos = struct.unpack('!f', resp[40:44])[0]
        return z_pos

    def z_ramp(self, target, ramp_time):
        """Ramp the z-position to target (m) over ramp_time (s).

        The ramp always spans ramp_time, so a shorter travel distance yields a
        smaller step size.

        Args:
            target: Target z-position (m).
            ramp_time: Total ramp duration (s); must be > 0.1 s.
        """
        if ramp_time > 0.1:
            start = self.z_pos_get()
            step = int(ramp_time / 0.001)
            set_pos = np.linspace(start, target, step)
            for pos in set_pos:
                self.z_pos_set(pos)
                time.sleep(0.001)
        else:
            print('Ramp too fast')
        return target

    def z_withdraw(self):
        """Withdraw the tip (switch off the Z controller and fully retract z)."""
        command = self.request_header_encode_raw(b'ZCtrl.Withdraw', body_size=8, send_back=1)
        command += struct.pack('>I', 1) + struct.pack('>i', -1)
        self.write_raw(command)
        resp = self.read()
        print(resp)

    # ---------------------------------------------------------------------------
    # Z controller
    # ---------------------------------------------------------------------------
    def z_controller_OnOffSet(self, on):
        """Turn the Z controller on (on=1) or off (on=0)."""
        command = self.request_header_encode_raw(b'ZCtrl.OnOffSet', body_size=4, send_back=0)
        command += struct.pack('>I', on)
        self.write_raw(command)

    def z_controller_OnOffGet(self):
        """Return the Z controller on/off status (1 on, 0 off)."""
        command = self.request_header_encode_raw(b'ZCtrl.OnOffGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        on = struct.unpack('!I', resp[40:44])[0]
        return on

    def z_controller_SwitchOffDelaySet(self, delay):
        """Set the Z controller switch-off delay (s)."""
        command = self.request_header_encode_raw(b'ZCtrl.SwitchOffDelaySet', body_size=4, send_back=0)
        command += struct.pack('>f', delay)
        self.write_raw(command)

    def z_controller_SwitchOffDelayGet(self):
        """Return the Z controller switch-off delay (s)."""
        command = self.request_header_encode_raw(b'ZCtrl.SwitchOffDelayGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        delay = struct.unpack('!f', resp[40:44])[0]
        return delay

    def z_controller_TipLiftSet(self, tip_lift):
        """Set the Z controller tip lift (m)."""
        command = self.request_header_encode_raw(b'ZCtrl.TipLiftSet', body_size=4, send_back=0)
        command += struct.pack('>f', tip_lift)
        self.write_raw(command)

    def z_controller_TipLiftGet(self):
        """Return the Z controller tip lift (m)."""
        command = self.request_header_encode_raw(b'ZCtrl.TipLiftGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        tip_lift = struct.unpack('!f', resp[40:44])[0]
        return tip_lift

    def z_controller_SetpntSet(self, setpnt):
        """Set the Z controller setpoint (in the feedback signal's units)."""
        command = self.request_header_encode_raw(b'ZCtrl.SetpntSet', body_size=4, send_back=0)
        command += struct.pack('>f', setpnt)
        self.write_raw(command)

    def z_controller_SetpntGet(self):
        """Return the Z controller setpoint (in the feedback signal's units)."""
        command = self.request_header_encode_raw(b'ZCtrl.SetpntGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        setpnt = struct.unpack('!f', resp[40:44])[0]
        return setpnt

    def z_controller_LimitsSet(self, z_high_limit, z_low_limit):
        """Set the Z controller high and low z limits (m)."""
        command = self.request_header_encode_raw(b'ZCtrl.LimitsSet', body_size=8, send_back=0)
        command += struct.pack('>f', z_high_limit)
        command += struct.pack('>f', z_low_limit)
        self.write_raw(command)

    def z_controller_LimitsGet(self):
        """Return the Z controller [z_high_limit, z_low_limit] (m)."""
        command = self.request_header_encode_raw(b'ZCtrl.LimitsGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        z_high_limit = struct.unpack('!f', resp[40:44])[0]
        z_low_limit = struct.unpack('!f', resp[44:48])[0]
        return [z_high_limit, z_low_limit]

    def z_controller_LimitsEnabledSet(self, status):
        """Enable (status=1) or disable (status=0) the Z controller z limits."""
        command = self.request_header_encode_raw(b'ZCtrl.LimitsEnabledSet', body_size=4, send_back=0)
        command += struct.pack('>I', status)
        self.write_raw(command)

    def z_controller_LimitsEnabledGet(self):
        """Return whether the Z controller z limits are enabled (1) or not (0)."""
        command = self.request_header_encode_raw(b'ZCtrl.LimitsEnabledGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        status = struct.unpack('!I', resp[40:44])[0]
        return status

    # ---------------------------------------------------------------------------
    # X-Y position
    # ---------------------------------------------------------------------------
    # Follow Me: move the tip to (x, y) at the speed set in the Follow Me tab.
    def XY_pos_get(self, wait_new=0):
        """Return the current tip XY position as a tuple (x, y) in meters."""
        command = self.request_header_encode_raw(b'FolMe.XYPosGet', body_size=4, send_back=1)
        command += struct.pack('>I', wait_new)
        self.write_raw(command)
        resp = self.read()
        return struct.unpack('!dd', resp[40:56])

    def XY_pos_set(self, x, y, wait_end=0):
        """Set the tip XY position to x (m) and y (m).

        Args:
            x: Target x position (m).
            y: Target y position (m).
            wait_end: 1 waits until the motion finishes, 0 returns immediately.
        """
        command = self.request_header_encode_raw(b'FolMe.XYPosSet', body_size=20,
                                                 send_back=0)
        command += struct.pack('>d', x) + struct.pack('>d', y) + struct.pack('>I', wait_end)
        self.write_raw(command)

    def tip_stop(self):
        """Immediately stop the tip XY motion (FolMe.Stop)."""
        command = self.request_header_encode_raw(b'FolMe.Stop', body_size=0, send_back=0)
        self.write_raw(command)

    # ---------------------------------------------------------------------------
    # Scan patterns
    # ---------------------------------------------------------------------------
    # Generate (x, y) point arrays from the pattern defined in the Scan module.
    # These methods return coordinate arrays in the default unit (m). Note that
    # Nanonis returns the pattern in different forms, so some post-processing is
    # applied below.
    def scan_grid_get(self):
        """Return the (x, y) coordinate arrays for the grid pattern (m)."""
        command = self.request_header_encode_raw(b'Pattern.GridGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        x_num, y_num = struct.unpack('!ii', resp[40:48])
        x_num += 1
        y_num += 1
        # Nanonis is returning number of intervals, which means pts = intervals + 1
        x_cen, y_cen = struct.unpack('!ff', resp[48:56])
        width, height, angle = struct.unpack('!fff', resp[56:68])
        x0 = np.linspace(-width / 2, width / 2, num=x_num)
        y0 = np.linspace(-height / 2, height / 2, num=y_num)
        theta = -angle * np.pi / 180  # Nanonis angle is negative for counterclockwise
        x = np.zeros(x_num * y_num)
        y = np.zeros(x_num * y_num)
        # The order of (x,y) points is a rasterized scanning of the grid
        for i in range(x_num * y_num):
            if int(i / x_num) % 2 == 0:
                x[i] = x0[i % x_num] * np.cos(theta) - y0[int(i / x_num)] * np.sin(theta) + x_cen
                y[i] = x0[i % x_num] * np.sin(theta) + y0[int(i / x_num)] * np.cos(theta) + y_cen
            else:
                x[i] = x0[x_num - 1 - i % x_num] * np.cos(theta) - y0[int(i / x_num)] * np.sin(theta) + x_cen
                y[i] = x0[x_num - 1 - i % x_num] * np.sin(theta) + y0[int(i / x_num)] * np.cos(theta) + y_cen
        return x, y

    def scan_line_get(self):
        """Return the (x, y) coordinate arrays for the line pattern (m)."""
        command = self.request_header_encode_raw(b'Pattern.LineGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        num = struct.unpack('!i', resp[40:44])
        x1, y1 = struct.unpack('!ff', resp[44:52])
        x2, y2 = struct.unpack('!ff', resp[52:60])
        x = np.linspace(x1, x2, num)
        y = np.linspace(y1, y2, num)
        return x, y

    def scan_cloud_get(self):
        """Return the (x, y) coordinate arrays for the cloud pattern (m)."""
        command = self.request_header_encode_raw(b'Pattern.CloudGet', body_size=0)
        self.write_raw(command)
        resp = self.read()
        num = struct.unpack('!i', resp[40:44])
        x = self.data_array_to_np(resp[44:44 + 4 * num], num, 'f')
        y = self.data_array_to_np(resp[44 + 4 * num:44 + 8 * num], num, 'f')
        return x, y

    # ---------------------------------------------------------------------------
    # Scanning
    # ---------------------------------------------------------------------------
    # Adapted from the NanonisTCP package.
    def Action(self, scan_action, scan_direction="up"):
        """
        Starts, stops, pauses, or resumes a scan.

        Parameters
        ----------
        scan_action : {"start", "stop", "pause", "resume"}
        scan_direction : {"up", "down"}
        """

        action_map = {"start": 0, "stop": 1, "pause": 2, "resume": 3}
        dir_map = {"down": 0, "up": 1}

        # Validate inputs (raise ValueError with a clear message if invalid)
        if scan_action not in action_map:
            raise ValueError(f"scan_action must be one of {list(action_map.keys())}, got {scan_action!r}")
        if scan_direction not in dir_map:
            raise ValueError(f"scan_direction must be one of {list(dir_map.keys())}, got {scan_direction!r}")

        action_code = action_map[scan_action]  # 2 bytes (uint16)
        dir_code = dir_map[scan_direction]  # 4 bytes (uint32)

        # === Make Header ===
        # Payload: 2 bytes (action) + 4 bytes (direction) = 6 bytes total
        command = self.request_header_encode_raw(b"Scan.Action", body_size=6, send_back=0)

        # === Arguments ===
        # Use big-endian: uint16 for action, uint32 for direction
        command += struct.pack(">H", action_code)
        command += struct.pack(">I", dir_code)

        # Send
        self.write_raw(command)

    def Scan_StatusGet(self):
        """Return the scan status (0: not scanning, 1: scanning)."""
        command = self.request_header_encode_raw(b'Scan.StatusGet', body_size=0, send_back=1)
        self.write_raw(command)
        resp = self.read()
        status = struct.unpack('!I', resp[40:44])[0]
        return status
        
    def WaitEndOfScan(self, timeout: int = -1):
        """
        Waits for the end-of-scan.
        Returns only when an end-of-scan or timeout occurs (whichever occurs first).

        Parameters
        ----------
        timeout : int
            Timeout in milliseconds. If -1, waits indefinitely.

        Returns
        -------
        [timeout_status, file_path_size, file_path] : list
            timeout_status : bool  -> True if function timed out, False otherwise
            file_path_size : int   -> number of bytes in file_path
            file_path      : str   -> path returned by the instrument
        """
        # === Make Header ===
        # Payload = 4 bytes (signed int32 timeout)
        command = self.request_header_encode_raw(b"Scan.WaitEndOfScan", body_size=4, send_back=1)

        # === Arguments ===
        # timeout is signed (allows -1 sentinel); big-endian int32
        command += struct.pack(">i", int(timeout))

        # Send
        self.write_raw(command)

        # === Receive Response ===
        # Expected payload layout:
        #   [0:4)  -> uint32 timeout_status (0/1)
        #   [4:8)  -> uint32 file_path_size (N)
        #   [8:8+N)-> bytes  file_path (UTF-8)
        payload = self.read()

        if len(payload) < 8:
            raise RuntimeError(f"Incomplete response payload: expected ≥8 bytes, got {len(payload)}")

        timeout_u32, file_path_size = struct.unpack(">II", payload[40:48])

        start = 48
        end = 48 + file_path_size

        file_path = payload[start:end].decode("utf-8", errors="replace")

        # If your transport provides an error trailer after `end`, validate it.
        if hasattr(self, "check_error"):
            self.check_error(payload, end)

        timeout_status = (timeout_u32 > 0)

        return [timeout_status, file_path_size, file_path]

    def FrameSet(self, x, y, w, h, angle: float = 0.0):
        """
        Configures the scan frame position and dimensions.

        Parameters
        ----------
        x : float
            Centre x coordinate (units per instrument settings)
        y : float
            Centre y coordinate
        w : float
            Scan frame width
        h : float
            Scan frame height
        angle : float, optional
            Scan frame angle in degrees. angle > 0: clockwise. Default = 0.
        """
        # === Make Header ===
        # Payload = 5 * float32 = 20 bytes
        command = self.request_header_encode_raw(b"Scan.FrameSet", body_size=20, send_back=0)

        # === Arguments ===
        # Use big-endian float32 for each value
        command += struct.pack(">fffff", float(x), float(y), float(w), float(h), float(angle))

        # Send
        self.write_raw(command)

    def FrameGet(self):
        """
        Returns the scan frame position and dimensions.

        Returns
        -------
        [x, y, w, h, angle] : list[float, float, float, float, float]
            x     : centre x coordinate
            y     : centre y coordinate
            w     : scan frame width
            h     : scan frame height
            angle : scan frame angle (degrees). angle > 0: clockwise
        """
        # === Make Header ===
        # Payload = 0 bytes, but we expect a response (5 * float32 = 20 bytes)
        command = self.request_header_encode_raw(b"Scan.FrameGet", body_size=0, send_back=1)

        # Send
        self.write_raw(command)

        # === Receive Response ===
        # Expected payload layout (big-endian):
        #   x, y, w, h, angle : 5 * float32 (20 bytes total)
        payload = self.read()
        if len(payload) < 20:
            raise RuntimeError(f"Incomplete response payload: expected 20 bytes, got {len(payload)}")

        x, y, w, h, angle = struct.unpack(">fffff", payload[40:60])

        return [x, y, w, h, angle]

    def BufferSet(self, channel_indexes=None, pixels=None, lines=None):
        """
        Configures the scan buffer parameters.

        Parameters
        ----------
        channel_indexes : list[int] or None
            Nanonis v < R11798: slot numbers.
            Nanonis v >= R11798: RT indexes in [0..127] (see Signals Manager or Signal.NamesGet).
            If None, keep current value.
        pixels : int or None
            Number of pixels per line (instrument forces a multiple of 16). If None, keep current value.
        lines : int or None
            Number of scan lines. If None, keep current value.
        """
        # Read current buffer settings to preserve unspecified fields
        _, buf_channel_indexes, buf_pixels, buf_lines = self.BufferGet()

        # Preserve current values if arguments are None
        channel_indexes = buf_channel_indexes if channel_indexes is None else channel_indexes
        pixels = buf_pixels if pixels is None else pixels
        lines = buf_lines if lines is None else lines

        # Basic validation / normalization
        if not isinstance(channel_indexes, (list, tuple)):
            raise ValueError("channel_indexes must be a list/tuple of integers")
        try:
            channel_indexes = [int(c) for c in channel_indexes]
            pixels = int(pixels)
            lines = int(lines)
        except Exception as e:
            raise ValueError(f"Invalid argument types for BufferSet: {e}")

        num_channels = len(channel_indexes)
        # Payload size: 4 (num_channels) + 4*num_channels (indexes) + 4 (pixels) + 4 (lines)
        body_size = 12 + 4 * num_channels

        # === Make Header ===
        command = self.request_header_encode_raw(b"Scan.BufferSet", body_size=body_size, send_back=0)

        # === Arguments (big-endian uint32s) ===
        command += struct.pack(">I", num_channels)
        if num_channels:
            command += b"".join(struct.pack(">I", c) for c in channel_indexes)
        command += struct.pack(">I", pixels)
        command += struct.pack(">I", lines)

        # Send
        self.write_raw(command)

    def BufferGet(self):
        """
        Returns the scan buffer parameters.

        Returns
        -------
        [num_channels, channel_indexes, pixels, lines] : list
            num_channels    : int
            channel_indexes : list[int]
            pixels          : int
            lines           : int
        """
        # === Make Header ===
        # Payload = 0 bytes, expecting a variable-length response
        command = self.request_header_encode_raw(b"Scan.BufferGet", body_size=0, send_back=1)

        # Send
        self.write_raw(command)

        # === Receive Response ===
        # Layout (big-endian):
        #   [0:4)            -> uint32 num_channels = N
        #   [4:4+4N)         -> N * uint32 channel_indexes
        #   [4+4N:4+4N+4)    -> uint32 pixels
        #   [8+4N:8+4N+4)    -> uint32 lines
        payload = self.read()
        if len(payload) < 4:
            raise RuntimeError(f"Incomplete response payload: expected ≥4 bytes, got {len(payload)}")

        (num_channels,) = struct.unpack(">I", payload[40:44])

        # Optional sanity check (Nanonis RT index list is typically ≤128)
        if num_channels > 1024:
            raise RuntimeError(f"Unreasonable channel count returned: {num_channels}")

        idx = 44

        if num_channels:
            channel_indexes = list(struct.unpack(f">{num_channels}I", payload[idx:idx + 4 * num_channels]))
        else:
            channel_indexes = []
        idx += 4 * num_channels

        pixels, lines = struct.unpack(">II", payload[idx:idx + 8])
        idx += 8

        # If your transport provides an error trailer after `idx`, validate it.
        if hasattr(self, "check_error"):
            self.check_error(payload, idx)

        return [num_channels, channel_indexes, pixels, lines]

    def PropsSet(self, continuous_scan: int = 0, bouncy_scan: int = 0, autosave: int = 0,
                 series_name: str = "%y%m%d_%H-%M-%S_SPM", comment: str = ""):
        """
        Configures scan parameters.

        Parameters
        ----------
        continuous_scan : int
            0: no change, 1: turn on, 2: turn off
        bouncy_scan : int
            0: no change, 1: turn on (alternate direction each EOS), 2: turn off
        autosave : int
            0: no change, 1: save all, 2: save next only, 3: save none
        series_name : str
            Base name for saved images
        comment : str
            Comment saved in the file
        """
        # Normalize & validate
        continuous_scan = int(continuous_scan)
        bouncy_scan = int(bouncy_scan)
        autosave = int(autosave)

        if continuous_scan not in (0, 1, 2):
            raise ValueError("continuous_scan must be 0 (no change), 1 (on), or 2 (off)")
        if bouncy_scan not in (0, 1, 2):
            raise ValueError("bouncy_scan must be 0 (no change), 1 (on), or 2 (off)")
        if autosave not in (0, 1, 2, 3):
            raise ValueError("autosave must be 0 (no change), 1 (all), 2 (next only), or 3 (none)")

        # Encode strings as UTF-8, lengths are in bytes
        series_b = series_name.encode("utf-8") if series_name else b""
        comment_b = comment.encode("utf-8") if comment else b""

        series_len = len(series_b)
        comment_len = len(comment_b)

        # Payload size:
        # 3*uint32 (settings) + uint32 (series_len) + series_b + uint32 (comment_len) + comment_b
        body_size = 12 + 4 + series_len + 4 + comment_len  # = 20 + series_len + comment_len

        # === Make Header ===
        command = self.request_header_encode_raw(b"Scan.PropsSet", body_size=body_size, send_back=0)

        # === Arguments (big-endian) ===
        # continuous_scan, bouncy_scan, autosave -> uint32 each
        command += struct.pack(">III", continuous_scan, bouncy_scan, autosave)

        # series_name
        command += struct.pack(">I", series_len)
        if series_len:
            command += series_b

        # comment
        command += struct.pack(">I", comment_len)
        if comment_len:
            command += comment_b

        # Send
        self.write_raw(command)

    def PropsGet(self):
        """
        Returns some of the scan parameters.

        Returns
        -------
        [continuous_scan, bouncy_scan, autosave, series_name, comment] : list
            continuous_scan : int
                0: off, 1: on
            bouncy_scan : int
                0: off (no direction change at EOS), 1: on (direction alternates each EOS)
            autosave : int
                0: save all, 1: save next only, 2: save none
            series_name : str
                Base name used for saved images
            comment : str
                Comment saved in the file
        """
        # === Make Header ===
        # No payload; expect response with:
        #   3*uint32 (continuous, bouncy, autosave)
        #   uint32 series_len
        #   series_len bytes (UTF-8)
        #   uint32 comment_len
        #   comment_len bytes (UTF-8)
        command = self.request_header_encode_raw(b"Scan.PropsGet", body_size=0, send_back=1)

        # Send
        self.write_raw(command)

        # Receive response
        payload = self.read()
        if len(payload) < 16:
            raise RuntimeError(f"Incomplete response payload: expected ≥16 bytes, got {len(payload)}")

        continuous_scan, bouncy_scan, autosave, series_len = struct.unpack(">IIII", payload[40:56])

        idx = 56
        # Ensure we have the whole series_name and next length
        if len(payload) < idx + series_len + 4:
            need = idx + series_len + 4
            raise RuntimeError(f"Incomplete response payload for series_name: need ≥{need} bytes, got {len(payload)}")

        series_name = payload[idx:idx + series_len].decode("utf-8", errors="replace")
        idx += series_len

        (comment_len,) = struct.unpack(">I", payload[idx:idx + 4])
        idx += 4

        if len(payload) < idx + comment_len:
            need = idx + comment_len
            raise RuntimeError(f"Incomplete response payload for comment: need ≥{need} bytes, got {len(payload)}")

        comment = payload[idx:idx + comment_len].decode("utf-8", errors="replace")
        idx += comment_len

        # If transport includes an error trailer after idx, validate it.
        if hasattr(self, "check_error"):
            self.check_error(payload, idx)

        return [continuous_scan, bouncy_scan, autosave, series_name, comment]

    def SpeedSet(self, fwd_speed: float = 0, bwd_speed: float = 0, fwd_line_time: float = 0, bwd_line_time: float = 0,
                 const_param: int = -1, speed_ratio: float = 0):
        """
        Configure scan speed parameters.

        Best practice is to define at least:
            (fwd_speed OR fwd_line_time)
          + (bwd_* counterpart OR speed_ratio)

        Examples:
            SpeedSet(fwd_speed=150e-9, bwd_speed=400e-9)
            SpeedSet(fwd_line_time=1, bwd_line_time=0.25)
            SpeedSet(fwd_speed=150e-9, speed_ratio=2)
            SpeedSet(fwd_line_time=1, speed_ratio=2)

        Parameters
        ----------
        fwd_speed : float
            Forward linear speed (m/s)
        bwd_speed : float
            Backward linear speed (m/s)
        fwd_line_time : float
            Forward time per line (s)
        bwd_line_time : float
            Backward time per line (s)
        const_param : int
            Which parameter to keep constant:
              0: No change (leave previous)
              1: Keep linear speed constant
              2: Keep time-per-line constant
            If <0, inferred from provided arguments.
        speed_ratio : float
            Backward/forward speed ratio
        """
        # Read current settings (used to fill in omitted values)
        buf_fwd_speed, buf_bwd_speed, buf_fwd_line_time, buf_bwd_line_time, buf_const_param, buf_speed_ratio = self.SpeedGet()

        # Infer const_param if not specified (<0)
        if const_param < 0:
            if fwd_speed and not fwd_line_time:
                const_param = 1
            if (not fwd_speed) and fwd_line_time:
                const_param = 2
        if const_param < 0:
            if bwd_speed and not bwd_line_time:
                const_param = 1
            if (not bwd_speed) and bwd_line_time:
                const_param = 2

        # Derive speed_ratio from provided pairs, if needed
        if bwd_speed and fwd_speed and not speed_ratio:
            speed_ratio = bwd_speed / fwd_speed
        if bwd_line_time and fwd_line_time and not speed_ratio:
            speed_ratio = fwd_line_time / bwd_line_time

        # Fill omitted fields with existing values
        if not fwd_speed:
            fwd_speed = buf_fwd_speed
        if not bwd_speed:
            bwd_speed = buf_bwd_speed
        if not fwd_line_time:
            fwd_line_time = buf_fwd_line_time
        if not bwd_line_time:
            bwd_line_time = buf_bwd_line_time
        if not speed_ratio:
            speed_ratio = buf_speed_ratio

        # If const_param still not set, default to "no change"
        if const_param < 0:
            const_param = 0

        # === Make Header ===
        # Payload = 4*float32 (16) + uint16 (2) + float32 (4) = 22 bytes
        command = self.request_header_encode_raw(b"Scan.SpeedSet", body_size=22, send_back=0)

        # === Arguments (big-endian) ===
        # fwd_speed, bwd_speed, fwd_line_time, bwd_line_time -> float32
        command += struct.pack(">ffff", float(fwd_speed), float(bwd_speed), float(fwd_line_time), float(bwd_line_time))
        # const_param -> uint16
        command += struct.pack(">H", int(const_param))
        # speed_ratio -> float32
        command += struct.pack(">f", float(speed_ratio))

        # Send
        self.write_raw(command)

    def SpeedGet(self):
        """
        Returns the scan speed parameters.

        Returns
        -------
        [fwd_speed, bwd_speed, fwd_line_time, bwd_line_time, const_param, speed_ratio] : list
            fwd_speed     : float  -> forward linear speed (m/s)
            bwd_speed     : float  -> backward linear speed (m/s)
            fwd_line_time : float  -> forward time per line (s)
            bwd_line_time : float  -> backward time per line (s)
            const_param   : int    -> 0: no change, 1: keep speed, 2: keep time/line
            speed_ratio   : float  -> backward/forward speed ratio
        """
        # === Make Header ===
        # No payload; expect response of 22 bytes:
        #   4*float32 + uint16 + float32
        command = self.request_header_encode_raw(b"Scan.SpeedGet", body_size=0, send_back=1)

        # Send
        self.write_raw(command)

        # === Receive Response ===
        payload = self.read()
        if len(payload) < 22:
            raise RuntimeError(f"Incomplete response payload: expected 22 bytes, got {len(payload)}")

        fwd_speed, bwd_speed, fwd_line_time, bwd_line_time, const_param, speed_ratio = struct.unpack(
            ">ffffHf", payload[40:62]
        )

        # If transport includes an error trailer after 22, validate it.
        if hasattr(self, "check_error"):
            self.check_error(payload, 22)

        return [fwd_speed, bwd_speed, fwd_line_time, bwd_line_time, const_param, speed_ratio]

    def FrameDataGrab(self, channel_index: int, data_direction: int):
        """
        Returns the scan data of the selected frame.

        Parameters
        ----------
        channel_index : int
            Index of the acquired channel (see Scan.BufferSet / Scan.BufferGet).
        data_direction : int
            0: backward, 1: forward

        Returns
        -------
        [channel_name, scan_data, scan_direction] : list
            channel_name  : str
            scan_data     : np.ndarray of shape (rows, cols), dtype=float32
            scan_direction: str  -> "down" or "up"
        """
        # Validate inputs
        if data_direction not in (0, 1):
            raise ValueError("data_direction must be 0 (backward) or 1 (forward)")
        channel_index = int(channel_index)

        # === Make Header ===
        # Payload = uint32 (channel_index) + uint32 (data_direction) = 8 bytes
        command = self.request_header_encode_raw(b"Scan.FrameDataGrab", body_size=8, send_back=1)

        # === Arguments (big-endian uint32s) ===
        command += struct.pack(">II", channel_index, data_direction)

        # Send
        self.write_raw(command)

        # === Receive Response ===
        # Expected layout (big-endian):
        #   [0:4)              -> uint32 channel_name_size (N)
        #   [4:4+N)            -> bytes channel_name (UTF-8)
        #   [4+N:8+N)          -> uint32 scan_data_rows (R)
        #   [8+N:12+N)         -> uint32 scan_data_columns (C)
        #   [12+N:12+N+4RC)    -> R*C * float32 scan_data
        #   [12+N+4RC:16+N+4RC)-> uint32 scan_direction (0=down, 1=up)
        payload = self.read()
        if len(payload) < 4:
            raise RuntimeError(f"Incomplete response payload: expected ≥4 bytes, got {len(payload)}")

        (name_len,) = struct.unpack(">I", payload[40:44])
        need_min = 4 + name_len + 8  # up to rows/cols
        if len(payload) < need_min:
            raise RuntimeError(
                f"Incomplete response payload before rows/cols: need ≥{need_min} bytes, got {len(payload)}"
            )

        name_start = 4
        name_end = 4 + name_len
        channel_name = payload[name_start:name_end].decode("utf-8", errors="replace")

        rows, cols = struct.unpack(">II", payload[name_end:name_end + 8])
        data_start = name_end + 8
        n_vals = int(rows) * int(cols)
        data_bytes = 4 * n_vals
        data_end = data_start + data_bytes

        if len(payload) < data_end + 4:
            # need at least the data block plus 4 bytes for scan_direction
            raise RuntimeError(
                f"Incomplete response payload for scan data: need ≥{data_end + 4} bytes, got {len(payload)}"
            )

        # Parse data as big-endian float32 and reshape
        if n_vals > 0:
            scan_data = np.frombuffer(payload[data_start:data_end], dtype=">f4").astype(np.float32, copy=False)
            scan_data = scan_data.reshape((rows, cols))
        else:
            scan_data = np.empty((0, 0), dtype=np.float32)

        # Scan direction
        (scan_dir_code,) = struct.unpack(">I", payload[data_end:data_end + 4])
        scan_direction = ["down", "up"][1 if scan_dir_code else 0]

        # If your transport appends an error trailer after data_end+4, validate it.
        if hasattr(self, "check_error"):
            self.check_error(payload, data_end + 4)

        return [channel_name, scan_data, scan_direction]

    # ---------------------------------------------------------------------------
    # XYZ positioner
    # ---------------------------------------------------------------------------
    # Adapted from the NanonisTCP package.
    def StartMove(self, direction, steps, wait_until_finished=True, group=1):
        """
        Moves the coarse positioning device (motor, piezo actuator...)

        Parameters
        ----------
        direction : selects in which direction to move. Note that depending on
                    your motor controller and setup only the Z axis or Z- axis
                    may work
                    Possible values: "X+","X-","Y+","Y-","Z+","Z-"
        steps     : number of motor steps to move in the specified direction
        wait_until_finished : defines if this function only returns when the
                              motor reaches its destination or the movement
                              stops
                              False: Return immediately
                              True:  Return once function has reached its
                                     destination
        group               : is the selection of groups defined in the motor
                              control module. If the motor doesn't support the
                              selection of groups, set it to 0
                              1: Group 1
                              2: Group 2
                              3: Group 3
                              4: Group 4
                              5: Group 5
                              6: Group 6

        """
        direction_dict = {"X+": 0,
                          "X-": 1,
                          "Y+": 2,
                          "Y-": 3,
                          "Z+": 4,
                          "Z-": 5,
                          }
        group -= 1  # Make this more sensible
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.StartMove', body_size=14, send_back=0)

        ## Arguments
        command += struct.pack(">4s", direction_dict[direction])
        command += struct.pack(">H", steps)
        command += struct.pack(">I", group)
        command += struct.pack("?", wait_until_finished)

        self.write_raw(command)

    def StartClosedLoop(self, abs_rel, target_x, target_y, target_z, wait_until_finished):
        """
        Moves the coarse positioning device (motor, piezo actuator...) in
        closed loop. This is not supported by all motor control modules

        Parameters
        ----------
        abs_rel             : selects if moving in relative or absolute mode
                              False: relative
                              True:  absolute
        target_x            : target position to move in x (m)
        target_y            : target position to move in y (m)
        target_z            : target position to move in z (m)
        wait_until_finished : defines if this function returns immediately or
                              after reaching destination
                              False: Return immediately
                              True:  Wait until position is reached before
                                     returning

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.StartClosedLoop', body_size=32, send_back=0)

        ## Arguments
        command += struct.pack("?", abs_rel)
        command += struct.pack(">d", target_x)
        command += struct.pack(">d", target_y)
        command += struct.pack(">d", target_z)
        command += struct.pack("?", wait_until_finished)

        self.write_raw(command)

    def StopMove(self):
        """
        Stops the motor motion

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.StopMove', body_size=0, send_back=0)

        self.write_raw(command)

    def PosGet(self):
        """
        Returns the positions of the motor control module.

        Returns
        -------
        x_pos : the x position (m)
        y_pos : the y position (m)
        z_pos : the z position (m)

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.PosGet', body_size=0, send_back=1)

        self.write_raw(command)

        resp = self.read()

        x_pos = struct.unpack('!d', resp[40:48])[0]
        y_pos = struct.unpack('!d', resp[48:56])[0]
        z_pos = struct.unpack('!d', resp[56:64])[0]

        return [x_pos, y_pos, z_pos]

    def StepCounterGet(self, reset_x=False, reset_y=False, reset_z=False):
        """
        Returns the step counter values of X, Y, Z.

        This function also allows to reset the step counters after reading
        their values through inputs reset_x,reset_y,reset_z

        Parameters
        ----------
        reset_x : reset the x counter (True: reset, False: don't reset)
        reset_y : reset the y counter (True: reset, False: don't reset)
        reset_z : reset the z counter (True: reset, False: don't reset)

        Returns
        -------
        counter_x : x step counter value
        counter_y : y step counter value
        counter_z : z step counter value

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.StepCounterGet', body_size=12, send_back=1)

        ## Arguments
        command += struct.pack("?", reset_x)
        command += struct.pack("?", reset_y)
        command += struct.pack("?", reset_z)

        self.write_raw(command)

        resp = self.read()

        counter_x = struct.unpack('!i', resp[40:44])[0]
        counter_y = struct.unpack('!i', resp[44:48])[0]
        counter_z = struct.unpack('!i', resp[48:52])[0]

        return [counter_x, counter_y, counter_z]

    def FreqAmpGet(self):
        """
        Returns the frequency (Hz) and amplitude (V) of the motor control
        module.

        This function is only available for PD5, PMD4, and Attocube ANC150
        devices.


        Returns
        -------
        frequency : frequency (Hz)
        amplitude : amplitude (V)

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.FreqAmpGet', body_size=0, send_back=1)

        self.write_raw(command)

        resp = self.read()

        frequency = struct.unpack('!f', resp[40:44])[0]
        amplitude = struct.unpack('!f', resp[44:48])[0]

        return [frequency, amplitude]

    def FreqAmpSet(self, frequency, amplitude, axis=0):
        """
        Sets the frequency (Hz) and amplitude (V) of the motor control

        Parameters
        ----------
        frequency : frequency (Hz)
        amplitude : amplitude (V)

        """
        ## Make Header
        command = self.request_header_encode_raw(b'Motor.FreqAmpSet', body_size=10, send_back=0)

        ## Arguments
        command += struct.pack(">f", frequency)
        command += struct.pack(">f", amplitude)
        command += struct.pack(">H", axis)

        self.write_raw(command)

    # ---------------------------------------------------------------------------
    # Lock-In
    # ---------------------------------------------------------------------------
    def lockin_freq_get(self, modulator: int):
        """Return the modulation frequency (Hz) of the given lock-in modulator."""
        command = self.request_header_encode_raw('LockIn.ModPhasFreqGet', body_size=4, send_back=1)
        command += struct.pack('>i', modulator)
        self.write_raw(command)
        response = self.read()
        value = struct.unpack('!d', response[40:48])[0]  # struct.unpack always returns a tuple; frequency in Hz
        return value

    def lockin_freq_set(self, modulator: int, freq: float):
        """Set the modulation frequency (Hz) of the given lock-in modulator."""
        command = self.request_header_encode_raw('LockIn.ModPhasFreqSet', body_size=12,
                                                 send_back=0)  # response is only an error message; not read
        command += struct.pack('>i', modulator) + struct.pack('>d', freq)
        self.write_raw(command)

    # ---------------------------------------------------------------------------
    # Oscilloscope
    # ---------------------------------------------------------------------------
    def Osci2T_data_get(self, data_to_get=0):
        """Return 2-channel oscilloscope data as (t0, dt, n, data_A, data_B)."""
        command = self.request_header_encode_raw('Osci2T.DataGet', body_size=2, send_back=1)
        command += struct.pack('>H', data_to_get)
        self.write_raw(command)
        response = self.read()
        data_t0 = struct.unpack('!d', response[40:48])[0]  # struct.unpack always returns a tuple
        data_dt = struct.unpack('!d', response[48:56])[0]
        data_Y_size = struct.unpack('!i', response[56:60])[0]
        data_bytes = 8 * data_Y_size
        data_A = self.data_array_to_np(response[60:60 + data_bytes], data_Y_size, 'd')
        data_B = self.data_array_to_np(response[60 + data_bytes:60 + 2 * data_bytes], data_Y_size, 'd')
        return data_t0, data_dt, data_Y_size, data_A, data_B


