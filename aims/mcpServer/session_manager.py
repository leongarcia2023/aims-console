"""Session manager for centering runs.

Creates a per-session directory tree and logs predictions, movements, images,
and final results to disk as JSON and CSV for later analysis.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import csv
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages centering sessions, including data logging and result storage.
    """
    
    def __init__(self, base_dir: str = "sessions"):
        """
        Initialize session manager.

        Args:
            base_dir: Base directory for storing session data
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        self.sessions = {}
        
    def create_session(self, config: Dict[str, Any]) -> str:
        """
        Create a new centering session.

        Args:
            config: Session configuration parameters
            
        Returns:
            str: Unique session ID
        """
        # Generate session ID from timestamp
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create session directory
        session_dir = self.base_dir / session_id
        session_dir.mkdir(exist_ok=True)
        
        # Create subdirectories
        (session_dir / "images").mkdir(exist_ok=True)
        (session_dir / "predictions").mkdir(exist_ok=True)
        (session_dir / "movements").mkdir(exist_ok=True)
        (session_dir / "results").mkdir(exist_ok=True)
        
        # Store session info
        session_info = {
            'session_id': session_id,
            'start_time': datetime.now().isoformat(),
            'config': config,
            'status': 'active'
        }
        
        # Save session info
        info_path = session_dir / "session_info.json"
        with open(info_path, 'w') as f:
            json.dump(session_info, f, indent=2)
            
        # Initialize predictions CSV
        predictions_path = session_dir / "predictions" / "predictions.csv"
        with open(predictions_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['iteration', 'x_um', 'y_um', 'confidence', 
                           'uncertainty_x', 'uncertainty_y', 'distance_from_center', 
                           'converged', 'timestamp'])
            
        # Initialize movements CSV
        movements_path = session_dir / "movements" / "movements.csv"
        with open(movements_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['iteration', 'x_steps', 'y_steps', 'success', 'timestamp'])
            
        self.sessions[session_id] = session_info
        logger.info(f"Created session: {session_id}")
        
        return session_id
        
    def save_image(self, session_id: str, image, filename: str):
        """
        Save an image to the session directory.

        Args:
            session_id: Session identifier
            image: Image array to save
            filename: Filename for the image
        """
        if session_id not in self.sessions:
            logger.error(f"Session {session_id} not found")
            return
            
        session_dir = self.base_dir / session_id
        image_path = session_dir / "images" / filename
        
        try:
            if image is not None:
                cv2.imwrite(str(image_path), image)
                logger.debug(f"Saved image: {filename}")
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            
    def log_prediction(self, session_id: str, iteration: int, 
                      position: np.ndarray, confidence: float,
                      uncertainty: np.ndarray, distance: float,
                      converged: bool):
        """
        Log a position prediction.

        Args:
            session_id: Session identifier
            iteration: Current iteration number
            position: Predicted (x, y) position
            confidence: Confidence score
            uncertainty: Uncertainty estimates
            distance: Distance from center
            converged: Whether converged
        """
        if session_id not in self.sessions:
            logger.error(f"Session {session_id} not found")
            return
            
        session_dir = self.base_dir / session_id
        predictions_path = session_dir / "predictions" / "predictions.csv"
        
        try:
            with open(predictions_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    iteration,
                    float(position[0]),
                    float(position[1]),
                    float(confidence),
                    float(uncertainty[0]) if len(uncertainty) > 0 else 0.0,
                    float(uncertainty[1]) if len(uncertainty) > 1 else 0.0,
                    float(distance),
                    converged,
                    datetime.now().isoformat()
                ])
        except Exception as e:
            logger.error(f"Failed to log prediction: {e}")
            
    def log_movement(self, session_id: str, iteration: int,
                    x_steps: float, y_steps: float, success: bool):
        """
        Log a stage movement.

        Args:
            session_id: Session identifier
            iteration: Current iteration number
            x_steps: X-axis steps
            y_steps: Y-axis steps
            success: Whether movement succeeded
        """
        if session_id not in self.sessions:
            logger.error(f"Session {session_id} not found")
            return
            
        session_dir = self.base_dir / session_id
        movements_path = session_dir / "movements" / "movements.csv"
        
        try:
            with open(movements_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    iteration,
                    float(x_steps),
                    float(y_steps),
                    success,
                    datetime.now().isoformat()
                ])
        except Exception as e:
            logger.error(f"Failed to log movement: {e}")
            
    def finalize_session(self, session_id: str, final_results: Dict[str, Any]):
        """
        Finalize a session and save final results.

        Args:
            session_id: Session identifier
            final_results: Final session results
        """
        if session_id not in self.sessions:
            logger.error(f"Session {session_id} not found")
            return
            
        session_dir = self.base_dir / session_id
        results_path = session_dir / "results" / "final_results.json"
        
        # Update session status
        self.sessions[session_id]['status'] = 'completed'
        self.sessions[session_id]['end_time'] = datetime.now().isoformat()
        
        # Save final results
        try:
            with open(results_path, 'w') as f:
                json.dump(final_results, f, indent=2)
                
            # Update session info
            info_path = session_dir / "session_info.json"
            with open(info_path, 'w') as f:
                json.dump(self.sessions[session_id], f, indent=2)
                
            logger.info(f"Finalized session: {session_id}")
        except Exception as e:
            logger.error(f"Failed to finalize session: {e}")
            
    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """
        Get summary of a session.

        Args:
            session_id: Session identifier
            
        Returns:
            dict: Session summary with statistics
        """
        if session_id not in self.sessions:
            return {'error': f'Session {session_id} not found'}
            
        session_dir = self.base_dir / session_id
        
        # Read session info
        info_path = session_dir / "session_info.json"
        with open(info_path, 'r') as f:
            session_info = json.load(f)
            
        # Read predictions
        predictions_path = session_dir / "predictions" / "predictions.csv"
        predictions = []
        if predictions_path.exists():
            with open(predictions_path, 'r') as f:
                reader = csv.DictReader(f)
                predictions = list(reader)
                
        # Read movements
        movements_path = session_dir / "movements" / "movements.csv"
        movements = []
        if movements_path.exists():
            with open(movements_path, 'r') as f:
                reader = csv.DictReader(f)
                movements = list(reader)
                
        summary = {
            'session_id': session_id,
            'session_info': session_info,
            'total_iterations': len(predictions),
            'total_movements': len(movements),
            'predictions': predictions,
            'movements': movements
        }
        
        # Add final position if available
        if predictions:
            last_pred = predictions[-1]
            summary['final_position'] = {
                'x_um': float(last_pred['x_um']),
                'y_um': float(last_pred['y_um']),
                'confidence': float(last_pred['confidence']),
                'converged': last_pred['converged'] == 'True'
            }
            
        return summary

