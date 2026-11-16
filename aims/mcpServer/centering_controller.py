"""
Centering controller for microscopy stage positioning.

Orchestrates the iterative centering of a feature on the microscope stage by
combining particle-filter position predictions with hardware control of the
Attocube stage and Nanonis controller. Each iteration captures an image,
computes the required movement, and executes it until the target converges to
the field-of-view center or the iteration budget is exhausted.
"""
import logging
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple, Optional, Any
import yaml

from hardware_connections import HardwareManager
from session_manager import SessionManager

logger = logging.getLogger(__name__)


class CenteringController:
    """
    Controls the iterative centering process using particle-filter predictions
    and hardware movement execution.
    """

    def __init__(self, config_path: Optional[str] = None, stage_config: Optional[Dict] = None):
        """
        Initialize the centering controller.

        Args:
            config_path: Path to configuration file (optional)
            stage_config: Pre-loaded stage configuration (optional, if not provided will load from file)
        """
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Use provided stage config or load from file
        if stage_config is not None:
            self.stage_config = stage_config
        else:
            self.stage_config = self._load_stage_config()

        # Initialize hardware manager
        self.hardware_manager = HardwareManager()
        self.hardware_ready = False

        # Session management
        self.session_manager = SessionManager()
        self.current_session = None

        # Centering state
        self.iteration = 0
        self.max_iterations = self.config.get('max_iterations', 10)
        self.convergence_threshold = self.config.get('convergence_threshold', 10.0)  # microns
        self.confidence_threshold = self.config.get('confidence_threshold', 0.7)
        self.is_converged = False

        # Last prediction
        self.last_position = None
        self.last_confidence = None
        self.last_uncertainty = None

    def _load_config(self, config_path: Optional[str]) -> Dict:
        """Load centering parameters from a YAML file.

        Returns an empty dict if the file does not exist.
        """
        # Use the default config path if none provided
        if config_path is None:
            config_path = "config/centering_config.yaml"

        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        return {}
    
    def _load_stage_config(self) -> Dict:
        """Load stage configuration for conversion factors."""
        stage_config_path = "config/stage_config.yaml"
        if Path(stage_config_path).exists():
            with open(stage_config_path, 'r') as f:
                return yaml.safe_load(f)
        return {}

    def initialize_hardware(self, nanonis_address: str = 'TCPIP0::localhost::6501::SOCKET',
                           attocube_address: str = 'COM6') -> bool:
        """
        Initialize hardware connections.

        Args:
            nanonis_address: Address for Nanonis controller
            attocube_address: Address for Attocube stage

        Returns:
            bool: True if hardware initialized successfully
        """
        try:
            self.hardware_ready = self.hardware_manager.connect_all(
                nanonis_address, attocube_address
            )

            # Load template image if enabled
            self._load_template_image()

            if self.hardware_ready:
                logger.info("Hardware initialized successfully")
            else:
                logger.error("Hardware initialization failed - server will not function")
            return self.hardware_ready
        except Exception as e:
            logger.error(f"Failed to initialize hardware: {e}")
            self.hardware_ready = False
            return False

    def start_session(self, max_iterations: Optional[int] = None,
                     convergence_threshold: Optional[float] = None,
                     confidence_threshold: Optional[float] = None) -> str:
        """
        Start a new centering session.

        Args:
            max_iterations: Maximum number of iterations
            convergence_threshold: Distance threshold for convergence (microns)
            confidence_threshold: Minimum confidence for full movement

        Returns:
            str: Session ID
        """
        # Update parameters if provided
        if max_iterations is not None:
            self.max_iterations = max_iterations
        if convergence_threshold is not None:
            self.convergence_threshold = convergence_threshold
        if confidence_threshold is not None:
            self.confidence_threshold = confidence_threshold

        # Create new session
        self.current_session = self.session_manager.create_session({
            'max_iterations': self.max_iterations,
            'convergence_threshold': self.convergence_threshold,
            'confidence_threshold': self.confidence_threshold,
            'hardware_ready': self.hardware_ready
        })

        # Reset state
        self.iteration = 0
        self.is_converged = False
        self.last_position = None
        self.last_confidence = None

        logger.info(f"Started centering session: {self.current_session}")
        return self.current_session

    def execute_movement(self, x_steps: float, y_steps: float) -> Dict[str, Any]:
        """
        Execute stage movement.

        Args:
            x_steps: X-axis movement in steps
            y_steps: Y-axis movement in steps

        Returns:
            dict: Movement execution status
        """
        if not self.current_session:
            raise RuntimeError("No active session. Call start_session() first.")

        try:
            if self.hardware_ready and self.hardware_manager.attocube:
                # Drive the physical Attocube stage
                logger.info(f"Executing hardware movement: x={x_steps}, y={y_steps}")
                self.hardware_manager.move_tip(x_steps, y_steps)
                success = True
                message = "Hardware movement executed"
            else:
                # Simulated movement
                logger.info(f"Simulated movement: x={x_steps}, y={y_steps}")
                success = True
                message = "Simulated movement executed"

            # Log movement
            self.session_manager.log_movement(
                self.current_session,
                self.iteration,
                x_steps,
                y_steps,
                success
            )

            return {
                'success': success,
                'message': message,
                'x_steps': float(x_steps),
                'y_steps': float(y_steps)
            }

        except Exception as e:
            logger.error(f"Movement execution failed: {e}")
            return {
                'success': False,
                'message': f"Movement failed: {str(e)}",
                'x_steps': float(x_steps),
                'y_steps': float(y_steps)
            }

    def get_status(self) -> Dict[str, Any]:
        """
        Get current centering status.

        Returns:
            dict: Current status including iteration, position, and convergence
        """
        if not self.current_session:
            return {
                'active': False,
                'message': 'No active session'
            }

        return {
            'active': True,
            'session_id': self.current_session,
            'iteration': self.iteration,
            'max_iterations': self.max_iterations,
            'converged': self.is_converged,
            'hardware_ready': self.hardware_ready,
            'last_position': self.last_position.tolist() if self.last_position is not None else None,
            'last_confidence': float(self.last_confidence) if self.last_confidence is not None else None,
            'last_uncertainty': self.last_uncertainty.tolist() if self.last_uncertainty is not None else None
        }

    def get_session_results(self) -> Dict[str, Any]:
        """
        Get final session results.

        Returns:
            dict: Session results and statistics
        """
        if not self.current_session:
            return {
                'error': 'No active session'
            }

        return self.session_manager.get_session_summary(self.current_session)

    def _calculate_movement(self, position: np.ndarray, confidence: float,
                           distance: float) -> Dict[str, float]:
        """
        Calculate movement steps based on position, confidence, and distance.

        Args:
            position: Current position (x, y) in microns
            confidence: Confidence score [0, 1]
            distance: Distance from center in microns

        Returns:
            dict: Movement in steps (x_steps, y_steps)
        """
        # Start with full movement toward center (negative of position)
        # Position is offset from center, so we move in opposite direction
        x_um = -position[0]
        y_um = -position[1]

        scale = 1.0

        # Apply scaling
        x_um *= scale
        y_um *= scale
        
        # Cap total movement distance at 100 microns while preserving direction
        movement_distance = np.sqrt(x_um**2 + y_um**2)
        if movement_distance > 100.0:
            # Scale down to 100 microns in the same direction
            scale_factor = 100.0 / movement_distance
            x_um *= scale_factor
            y_um *= scale_factor

        # Convert to steps using separate conversion factors for X and Y
        # microns_per_step = how many microns one step moves
        # steps = distance_um / microns_per_step

        # For low temperature
        microns_per_step_x = self.stage_config.get('attocube', {}).get('microns_per_step_x', -0.28)
        microns_per_step_y = self.stage_config.get('attocube', {}).get('microns_per_step_y', 0.38)
        
        x_steps = x_um / microns_per_step_x
        y_steps = y_um / microns_per_step_y
        
        # Convert to integers
        x_steps = int(round(x_steps))
        y_steps = int(round(y_steps))

        logger.debug(f"Movement calculation: position={position}, confidence={confidence:.3f}, "
                    f"scale={scale:.2f}, movement_um=({x_um:.2f}, {y_um:.2f}), steps=({x_steps}, {y_steps}), "
                    f"conversion=({microns_per_step_x}, {microns_per_step_y}) μm/step")

        return {
            'x_steps': x_steps,
            'y_steps': y_steps,
            'scale_applied': scale
        }

    def _capture_image(self):
        """
        Capture image from microscope.

        Returns:
            np.ndarray: Captured image, or None if capture fails
        """
        if not self.hardware_ready or not self.hardware_manager.nanonis:
            logger.error("Hardware not ready - cannot capture image")
            return None

        try:
            # Acquire and background-subtract an image from Nanonis
            logger.info("Capturing image from Nanonis")
            image = self.hardware_manager.get_sxm_image()
            logger.info(f"Image captured successfully, shape: {image.shape}")
            return image
        except Exception as e:
            logger.error(f"Image capture failed: {e}")
            return None


    def _load_template_image(self):
        """Load template image into particle filter (legacy, no longer used).

        Retained for backwards compatibility; the current particle filter loads
        templates directly from its own Config.
        """
        pass

    def cleanup(self):
        """Clean up resources and disconnect hardware."""
        if self.hardware_manager:
            self.hardware_manager.disconnect_all()
        logger.info("Centering controller cleanup completed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()

