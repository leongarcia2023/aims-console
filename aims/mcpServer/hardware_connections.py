"""Hardware connection layer for the MIM microscopy stack.

Provides thin connection helpers and a :class:`HardwareManager` that manage the
two instruments used by the centering/scanning workflow:

* **Nanonis** SPM controller (tip approach/lift, scanning, signal readout) over
  a VISA/TCP socket, via the local :mod:`Nanonis` driver.
* **Attocube** ANC300 coarse XY stage (stepper positioning), via the Attocube
  SDK or a ``pylablib`` fallback.

Both hardware libraries are optional: if an import fails the module degrades
gracefully (``NANONIS_AVAILABLE`` / ``ATTOCUBE_AVAILABLE`` flags) so the MCP
server can still start and report a clear error when a hardware tool is called.
"""
import pyvisa
import logging
import time
import numpy as np

logger = logging.getLogger(__name__)

# Import hardware libraries
try:
    # Try importing from local directory first
    try:
        from Nanonis import Nanonis
    except ImportError:
        # Try importing from thing/mcp_server_portable
        import sys
        import os
        thing_path = os.path.join(os.path.dirname(__file__), '..', 'thing', 'mcp_server_portable')
        if os.path.exists(thing_path):
            sys.path.insert(0, thing_path)
            from Nanonis import Nanonis
        else:
            raise ImportError("Nanonis.py not found")

    from pySPM import SXM
    NANONIS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Nanonis library not available: {e}")
    NANONIS_AVAILABLE = False

try:
    # Try direct import first (may work if Attocube SDK is installed)
    try:
        import Attocube
    except ImportError:
        # Fallback to pylablib
        try:
            from pylablib.devices import Attocube
        except (ImportError, AttributeError, FileNotFoundError) as e:
            raise ImportError(f"Neither direct Attocube import nor pylablib available: {e}")
    ATTOCUBE_AVAILABLE = True
except (ImportError, FileNotFoundError) as e:
    logger.warning(f"Attocube library not available: {e}")
    logger.info("To use Attocube hardware, install Attocube SDK from manufacturer, or pip install pylablib with FTDI drivers (ftd2xx)")
    ATTOCUBE_AVAILABLE = False


def connect_Nanonis(address='TCPIP0::localhost::6501::SOCKET'):
    """
    Connect to Nanonis controller.

    Args:
        address: VISA address for Nanonis controller

    Returns:
        Nanonis: Connected Nanonis controller instance

    Raises:
        ImportError: If Nanonis library is not available
        Exception: If connection fails
    """
    if not NANONIS_AVAILABLE:
        raise ImportError("Nanonis library is not installed. Please install it to use Nanonis hardware.")

    try:
        logger.info(f"Connecting to Nanonis at {address}")
        rm = pyvisa.ResourceManager()
        nanonis = Nanonis(rm, address)
        logger.info("Successfully connected to Nanonis")
        return nanonis
    except Exception as e:
        logger.error(f"Failed to connect to Nanonis: {e}")
        raise


def connect_Attocube(address='COM6'):
    """
    Connect to Attocube ANC300 stage controller.

    Args:
        address: COM port address for Attocube controller

    Returns:
        tuple: (attocube, available_axes)
            - attocube: Connected Attocube ANC300 instance
            - available_axes: List of available axes

    Raises:
        ImportError: If Attocube library is not available
        Exception: If connection fails
    """
    if not ATTOCUBE_AVAILABLE:
        raise ImportError("Attocube library is not installed. Please install it to use Attocube hardware.")

    try:
        logger.info(f"Connecting to Attocube at {address}")
        # Use the imported Attocube module (either direct or from pylablib)
        attocube = Attocube.ANC300(address)
        available_axes = attocube.update_available_axes()
        logger.info(f"Successfully connected to Attocube. Available axes: {available_axes}")
        return attocube, available_axes
    except Exception as e:
        logger.error(f"Failed to connect to Attocube: {e}")
        raise


class HardwareManager:
    """
    Manages hardware connections for Nanonis and Attocube with extended functionality.
    """

    def __init__(self):
        """Initialize with no active hardware connections."""
        self.nanonis = None
        self.attocube = None
        self.available_axes = None
        self.hardware_ready = False

    def connect_all(self, nanonis_address='TCPIP0::localhost::6501::SOCKET',
                   attocube_address='COM6'):
        """
        Connect to all hardware devices.

        Args:
            nanonis_address: VISA address for Nanonis
            attocube_address: COM port for Attocube

        Returns:
            bool: True if all connections successful
        """
        success = True

        # Try to connect to Nanonis
        try:
            self.nanonis = connect_Nanonis(nanonis_address)
            logger.info("Nanonis connected successfully")
        except Exception as e:
            logger.error(f"Failed to connect Nanonis: {e}")
            success = False

        # Try to connect to Attocube
        try:
            self.attocube, self.available_axes = connect_Attocube(attocube_address)
            logger.info("Attocube connected successfully")
        except Exception as e:
            logger.error(f"Failed to connect Attocube: {e}")
            success = False

        self.hardware_ready = success
        return success

    # Attocube stage movement
    def move_tip(self, x_steps, y_steps, x_volt=60, y_volt=60, x_freq=100, y_freq=100):
        """Move tip using Attocube stage."""
        if not self.attocube:
            raise RuntimeError("Attocube not connected")

        return move_tip_attocube(self.attocube, x_steps, y_steps, x_volt, y_volt, x_freq, y_freq)

    # Nanonis tip approach and scan operations
    def approach_and_lift(self, tip_lift=500e-9, high_limit=3e-6, low_limit=-3e-6):
        """Approach and lift tip using Nanonis."""
        if not self.nanonis:
            raise RuntimeError("Nanonis not connected")

        return approach_and_lift_nanonis(self.nanonis, tip_lift, high_limit, low_limit)

    def scan_and_retract(self, series_name, direction="up", scan_speed=1.9e-6, pixels=128, lines=40):
        """Scan and retract using Nanonis."""
        if not self.nanonis:
            raise RuntimeError("Nanonis not connected")

        return scan_and_retract_nanonis(self.nanonis, series_name, direction, scan_speed, pixels, lines)

    def get_sxm_image(self, id="0001", channel="MIM-Im_AC-X", direction="forward"):
        """Get SXM image with background subtraction."""
        if not self.nanonis:
            raise RuntimeError("Nanonis not connected")

        return get_sxm_image_processed(self.nanonis, id, channel, direction)

    def disconnect_all(self):
        """Disconnect from all hardware devices"""
        if self.nanonis is not None:
            try:
                # Close Nanonis connection if it has a close method
                if hasattr(self.nanonis, 'close'):
                    self.nanonis.close()
                logger.info("Nanonis disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting Nanonis: {e}")

        if self.attocube is not None:
            try:
                # Close Attocube connection if it has a close method
                if hasattr(self.attocube, 'close'):
                    self.attocube.close()
                logger.info("Attocube disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting Attocube: {e}")

        self.nanonis = None
        self.attocube = None
        self.available_axes = None
        self.hardware_ready = False

    def __enter__(self):
        """Context-manager entry: return the manager itself."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context-manager exit: disconnect all hardware on the way out."""
        self.disconnect_all()
        return False


# ---------------------------------------------------------------------------
# Standalone low-level hardware routines
# ---------------------------------------------------------------------------
def move_tip_attocube(attocube, x_steps, y_steps, x_volt=60, y_volt=60, x_freq=100, y_freq=100):
    """Move tip in x and y directions."""
    # Move in the X direction
    xaxis_number = 4
    attocube.set_mode(xaxis_number, "stp")
    attocube.set_frequency(xaxis_number, x_freq)
    attocube.set_voltage(xaxis_number, x_volt)
    attocube.move_by(xaxis_number, x_steps)
    attocube.wait_move(xaxis_number)
    time.sleep(0.1)
    attocube.set_mode(xaxis_number, "gnd")
    time.sleep(3)
    # Move in the Y direction
    yaxis_number = 5
    attocube.set_mode(yaxis_number, "stp")
    attocube.set_frequency(yaxis_number, y_freq)
    attocube.set_voltage(yaxis_number, y_volt)
    attocube.move_by(yaxis_number, y_steps)
    attocube.wait_move(yaxis_number)
    time.sleep(0.1)
    attocube.set_mode(yaxis_number, "gnd")

def approach_and_lift_nanonis(nanonis, tip_lift=500e-9, high_limit=12e-6, low_limit=4e-6):
    """Approach and lift tip procedure."""
    channel = "OC M1 Freq. Shift (Hz)"
    index = nanonis.signals.index(channel)
    nanonis.PLL_freq_shift_auto_center(1)
    nanonis.XY_pos_set(0, 0, wait_end=1)
    nanonis.z_controller_LimitsSet(high_limit, low_limit)
    nanonis.z_controller_OnOffSet(1)
    freq_shift = nanonis.sig_val_get_by_index(index)
    while nanonis.z_controller_SetpntGet() > freq_shift:
        freq_shift = nanonis.sig_val_get_by_index(index)
    approach_z = nanonis.z_pos_get()
    nanonis.z_withdraw()
    while nanonis.z_pos_get() < high_limit:
        time.sleep(0.1)
    low_limit = approach_z + tip_lift
    nanonis.z_controller_LimitsSet(high_limit, low_limit)
    nanonis.z_controller_OnOffSet(1)
    time.sleep(60)

def scan_and_retract_nanonis(nanonis, series_name, direction="up", scan_speed=1.9e-6, pixels=128, lines=40):
    """Scan and retract procedure."""
    nanonis.SpeedSet(fwd_speed=scan_speed, bwd_speed=scan_speed)
    nanonis.BufferSet(pixels=pixels, lines=lines)
    nanonis.PropsSet(series_name=series_name)
    nanonis.Action("start", direction)
    nanonis.WaitEndOfScan()
    nanonis.z_withdraw()

def subtract_linear_background(z, deg=1, mask=None):
    """
    Remove horizontal stripes via row-wise polynomial fit.
    Returns (destriped_image, None, None) for compatibility with old (plane, coef) API.
    """
    from stripe_removal import remove_stripes_row_poly
    out = remove_stripes_row_poly(z, deg=deg, mask=mask)
    return out, None, None


def get_sxm_image_processed(nanonis, id="0001", channel="MIM-Im_AC-X", direction="forward"):
    """Get SXM image with row-wise polynomial stripe removal (deg=1)."""
    from stripe_removal import remove_stripes_row_poly
    filepath = nanonis.session_path_get() + r"\{}{}.sxm".format(nanonis.PropsGet()[3], id)
    sxm = SXM(filepath)
    scan = sxm.get_channel(channel, direction=direction)
    img = scan.pixels
    img = remove_stripes_row_poly(img, deg=1)
    return img

