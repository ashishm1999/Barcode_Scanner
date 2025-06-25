import serial
import time
import requests
import json
import threading
import re
import os
import gc  # Garbage collector
from datetime import datetime, timezone
from collections import defaultdict, deque
import logging

# Configure logging (console only, no file logging to prevent memory/disk issues)
logging.basicConfig(
    level=logging.WARNING,  # Reduced logging for 24/7 operation
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BarcodeTracker:
    def __init__(self, port='/dev/ttyUSB0', baudrate=9600, api_base_url='https://goldenfries-api-6cd6d0acd119.herokuapp.com'):
        self.port = port
        self.baudrate = baudrate
        self.api_base_url = api_base_url
        self.ser = None
        
        # Persistent storage for power-loss protection
        self.buffer_file = '/home/robotics/barcode_buffer.json'
        self.current_data_file = '/home/robotics/current_barcode_data.json'
        
        # Data storage with memory limits
        self.barcode_counts = defaultdict(int)  # {barcode: count}
        self.all_scanned_barcodes = deque(maxlen=100)  # Limited to 100 recent scans
        self.total_box_count = 0
        
        # Smart offline buffering - consolidates instead of dropping
        self.offline_buffer = deque()  # No limit - but we'll consolidate
        self.network_available = True
        self.max_buffer_size = 500  # Trigger consolidation at 500 entries
        
        # Threading control
        self.running = False
        self.data_lock = threading.Lock()
        
        # Timing - 10 minutes
        self.update_interval = 10 * 60  # 10 minutes in seconds
        self.last_update_time = time.time()
        self.network_check_interval = 30  # Check network every 30 seconds
        self.last_network_check = time.time()
        
        # Memory management for long-running operation
        self.gc_interval = 3600  # Force garbage collection every hour
        self.last_gc_time = time.time()
        self.scan_count_since_gc = 0
        
        # Load any existing data from previous session
        self.load_persistent_data()

    def load_persistent_data(self):
        """Load any data from previous session that survived power loss"""
        try:
            # Load offline buffer
            if os.path.exists(self.buffer_file):
                with open(self.buffer_file, 'r') as f:
                    buffer_data = json.load(f)
                    self.offline_buffer = deque(buffer_data)
                    logger.warning(f"Loaded {len(self.offline_buffer)} entries from persistent buffer")
                    
            # Load current session data
            if os.path.exists(self.current_data_file):
                with open(self.current_data_file, 'r') as f:
                    session_data = json.load(f)
                    self.barcode_counts = defaultdict(int, session_data.get('barcode_counts', {}))
                    self.total_box_count = session_data.get('total_box_count', 0)
                    self.last_update_time = session_data.get('last_update_time', time.time())
                    logger.warning(f"Restored session: {self.total_box_count} boxes, {len(self.barcode_counts)} unique barcodes")
                    
        except Exception as e:
            logger.error(f"Error loading persistent data: {e}")
            # Continue with empty data if loading fails
    
    def save_persistent_data(self):
        """Save current data to survive power loss"""
        try:
            # Only save offline buffer to separate file
            self.save_buffer_to_disk()
            
            # Save current session data only if we have unsent data
            if self.total_box_count > 0:
                session_data = {
                    'barcode_counts': dict(self.barcode_counts),
                    'total_box_count': self.total_box_count,
                    'last_update_time': self.last_update_time,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                with open(self.current_data_file, 'w') as f:
                    json.dump(session_data, f)
            else:
                # No current data to save, remove file if it exists
                self.clear_persistent_data()
                
        except Exception as e:
            logger.error(f"Error saving persistent data: {e}")
    
    def clear_persistent_data(self):
        """Clear persistent data after successful API send"""
        try:
            if os.path.exists(self.current_data_file):
                os.remove(self.current_data_file)
                logger.warning("Cleared current session data file")
        except Exception as e:
            logger.error(f"Error clearing persistent data: {e}")
    
    def save_buffer_to_disk(self):
        """Save only the offline buffer to disk"""
        try:
            if self.offline_buffer:
                with open(self.buffer_file, 'w') as f:
                    json.dump(list(self.offline_buffer), f)
            else:
                # Buffer is empty, remove the file
                self.clear_buffer_file()
        except Exception as e:
            logger.error(f"Error saving buffer to disk: {e}")
    
    def clear_buffer_file(self):
        """Remove the buffer file when no longer needed"""
        try:
            if os.path.exists(self.buffer_file):
                os.remove(self.buffer_file)
                logger.warning("Cleared persistent buffer file")
        except Exception as e:
            logger.error(f"Error clearing buffer file: {e}")
    
    def perform_memory_maintenance(self):
        """Perform memory cleanup for long-running operation"""
        current_time = time.time()
        
        # Force garbage collection every hour or every 1000 scans
        if (current_time - self.last_gc_time >= self.gc_interval or 
            self.scan_count_since_gc >= 1000):
            
            # Clear old barcode history beyond the deque limit
            if len(self.all_scanned_barcodes) >= 90:  # Near the limit of 100
                # Keep only the most recent 50
                while len(self.all_scanned_barcodes) > 50:
                    self.all_scanned_barcodes.popleft()
            
            # Force garbage collection
            collected = gc.collect()
            self.last_gc_time = current_time
            self.scan_count_since_gc = 0
            
            logger.warning(f"Memory maintenance: Collected {collected} objects")
    
    def consolidate_buffer(self):
        """Consolidate buffer entries to save memory during long offline periods"""
        if len(self.offline_buffer) < self.max_buffer_size:
            return
        
        logger.warning(f"Consolidating buffer: {len(self.offline_buffer)} entries")
        
        # Group entries by barcode
        consolidated = defaultdict(int)
        log_entries = []
        oldest_timestamp = None
        newest_timestamp = None
        
        # Process all production data entries
        new_buffer = deque()
        for entry in self.offline_buffer:
            if entry['type'] == 'production_data':
                # Extract barcode and count
                for barcode, count in entry['barcode_data'].items():
                    consolidated[barcode] += count
                
                # Track time range
                timestamp = entry['timestamp']
                if oldest_timestamp is None or timestamp < oldest_timestamp:
                    oldest_timestamp = timestamp
                if newest_timestamp is None or timestamp > newest_timestamp:
                    newest_timestamp = timestamp
            else:
                # Keep log entries as-is
                log_entries.append(entry)
        
        # Create consolidated production entries (one per barcode)
        for barcode, total_count in consolidated.items():
            consolidated_entry = {
                'type': 'production_data',
                'box_count': total_count,
                'barcode_data': {barcode: total_count},
                'timestamp': newest_timestamp or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'consolidated': True,
                'time_range': f"{oldest_timestamp} to {newest_timestamp}"
            }
            new_buffer.append(consolidated_entry)
        
        # Add back log entries
        for log_entry in log_entries:
            new_buffer.append(log_entry)
        
        # Replace old buffer
        self.offline_buffer = new_buffer
        
        # Save consolidated buffer to disk
        self.save_buffer_to_disk()
        
        logger.warning(f"Buffer consolidated: {len(consolidated)} barcode types, {len(log_entries)} log entries")
        
        # Force garbage collection after major operation
        gc.collect()
        """Perform memory cleanup for long-running operation"""
        current_time = time.time()
        
        # Force garbage collection every hour or every 1000 scans
        if (current_time - self.last_gc_time >= self.gc_interval or 
            self.scan_count_since_gc >= 1000):
            
            # Clear old barcode history beyond the deque limit
            if len(self.all_scanned_barcodes) >= 90:  # Near the limit of 100
                # Keep only the most recent 50
                while len(self.all_scanned_barcodes) > 50:
                    self.all_scanned_barcodes.popleft()
            
            # Force garbage collection
            collected = gc.collect()
            self.last_gc_time = current_time
            self.scan_count_since_gc = 0
            
            logger.warning(f"Memory maintenance: Collected {collected} objects")
    
    def log_to_api(self, error_message, reason, log_type="Barcode Scanner"):
        """Send log entry to API with offline buffering support - minimal console logging"""
        if self.network_available:
            success = self._send_log_entry(error_message, reason, log_type)
            if success:
                return True
            else:
                # Network might be down, update status
                self.network_available = False
        
        # Buffer the log entry for later
        buffered_entry = {
            'type': 'log_entry',
            'error': error_message,
            'reason': reason,
            'log_type': log_type,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        self.offline_buffer.append(buffered_entry)
        # Reduced console logging for 24/7 operation
        return False
        """Load any data from previous session that survived power loss"""
        try:
            # Load offline buffer
            if os.path.exists(self.buffer_file):
                with open(self.buffer_file, 'r') as f:
                    buffer_data = json.load(f)
                    self.offline_buffer = deque(buffer_data)
                    logger.info(f"Loaded {len(self.offline_buffer)} entries from persistent buffer")
                    
            # Load current session data
            if os.path.exists(self.current_data_file):
                with open(self.current_data_file, 'r') as f:
                    session_data = json.load(f)
                    self.barcode_counts = defaultdict(int, session_data.get('barcode_counts', {}))
                    self.total_box_count = session_data.get('total_box_count', 0)
                    self.last_update_time = session_data.get('last_update_time', time.time())
                    logger.info(f"Restored session: {self.total_box_count} boxes, {len(self.barcode_counts)} unique barcodes")
                    
        except Exception as e:
            logger.error(f"Error loading persistent data: {e}")
            # Continue with empty data if loading fails
    
    def save_persistent_data(self):
        """Save current data to survive power loss"""
        try:
            # Only save offline buffer to separate file
            self.save_buffer_to_disk()
            
            # Save current session data only if we have unsent data
            if self.total_box_count > 0:
                session_data = {
                    'barcode_counts': dict(self.barcode_counts),
                    'total_box_count': self.total_box_count,
                    'last_update_time': self.last_update_time,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                with open(self.current_data_file, 'w') as f:
                    json.dump(session_data, f)
            else:
                # No current data to save, remove file if it exists
                self.clear_persistent_data()
                
        except Exception as e:
            logger.error(f"Error saving persistent data: {e}")
    
    def clear_persistent_data(self):
        """Clear persistent data after successful API send"""
        try:
            if os.path.exists(self.current_data_file):
                os.remove(self.current_data_file)
                logger.info("Cleared current session data file")
        except Exception as e:
            logger.error(f"Error clearing persistent data: {e}")
        """Send log entry to API with offline buffering support"""
        if self.network_available:
            success = self._send_log_entry(error_message, reason, log_type)
            if success:
                return True
            else:
                # Network might be down, update status
                self.network_available = False
        
        # Buffer the log entry for later
        buffered_entry = {
            'type': 'log_entry',
            'error': error_message,
            'reason': reason,
            'log_type': log_type,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        self.offline_buffer.append(buffered_entry)
        logger.warning(f"Buffered log entry - Network: {'OFFLINE' if not self.network_available else 'ONLINE'}")
        return False
    
    def _send_log_entry(self, error_message, reason, log_type="Barcode Scanner", timestamp=None):
        """Internal method to send log entry to API"""
        try:
            url = f"{self.api_base_url}/api/log/CreateLog"
            
            payload = {
                "logDate": timestamp or datetime.now(timezone.utc).isoformat(),
                "error": error_message,
                "reason": reason,
                "type": log_type
            }
            
            headers = {
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            # Don't use log_to_api here to avoid recursion
            logger.error(f"Failed to send log to API: {e}")
            logger.error(f"Original error: {error_message} - Reason: {reason}")
            return False
    
    def extract_barcode_from_scanner_output(self, scanner_output):
        """
        Extract barcode from scanner output format like:
        'Scanned barcode: 100% P:090 "9369998125494" L:13'
        
        Returns just the barcode number: 9369998125494
        """
        try:
            # Look for quoted barcode pattern
            match = re.search(r'"([^"]+)"', scanner_output)
            if match:
                return match.group(1)
            
            # Fallback: look for numeric sequence after percentage
            match = re.search(r'\d+%.*?"?([0-9]+)"?', scanner_output)
            if match:
                return match.group(1)
            
            # Another fallback: extract any long numeric sequence
            match = re.search(r'([0-9]{10,})', scanner_output)
            if match:
                return match.group(1)
            
            return None
            
        except Exception as e:
            self.log_to_api(
                f"Error extracting barcode from: {scanner_output}",
                f"Regex parsing failed: {str(e)}"
            )
            return None

    def is_valid_product_barcode(self, barcode):
        """
        Check if barcode is valid for counting (starts with '9369998')
        """
        if not barcode or barcode == "No Read":
            return False
        
        return barcode.startswith('9369998')
    
    def check_network_connectivity(self):
        """Check if network/internet is available"""
        try:
            # Try to reach a reliable endpoint (Google DNS)
            import socket
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except OSError:
            return False
    
    def update_network_status(self):
        """Update network status and log changes"""
        current_time = time.time()
        if current_time - self.last_network_check >= self.network_check_interval:
            old_status = self.network_available
            self.network_available = self.check_network_connectivity()
            self.last_network_check = current_time
            
            if old_status != self.network_available:
                if self.network_available:
                    logger.info("Network connection restored!")
                    # Try to process offline buffer when network comes back
                    self.process_offline_buffer()
                else:
                    logger.warning("Network connection lost - buffering data offline")
    
    def process_offline_buffer(self):
        """Process buffered data when network comes back online"""
        if not self.offline_buffer:
            return
        
        logger.info(f"Processing {len(self.offline_buffer)} buffered entries...")
        successful_sends = 0
        
        # Process buffered entries
        while self.offline_buffer and self.network_available:
            try:
                buffered_entry = self.offline_buffer.popleft()
                
                if buffered_entry['type'] == 'production_data':
                    success = self._send_production_data(
                        buffered_entry['box_count'],
                        buffered_entry['barcode_data'],
                        buffered_entry['timestamp']
                    )
                elif buffered_entry['type'] == 'log_entry':
                    success = self._send_log_entry(
                        buffered_entry['error'],
                        buffered_entry['reason'],
                        buffered_entry['log_type'],
                        buffered_entry['timestamp']
                    )
                
                if success:
                    successful_sends += 1
                    # Update persistent buffer after successful send
                    self.save_buffer_to_disk()
                else:
                    # If send fails, put it back in buffer and stop processing
                    self.offline_buffer.appendleft(buffered_entry)
                    self.network_available = False
                    break
                    
            except Exception as e:
                logger.error(f"Error processing buffered entry: {e}")
        
        if successful_sends > 0:
            logger.info(f"Successfully sent {successful_sends} buffered entries")
        
        if self.offline_buffer:
            logger.warning(f"{len(self.offline_buffer)} entries still in buffer")
        else:
            # Buffer is empty, remove the file
            self.clear_buffer_file()
    
    def save_buffer_to_disk(self):
        """Save only the offline buffer to disk"""
        try:
            if self.offline_buffer:
                with open(self.buffer_file, 'w') as f:
                    json.dump(list(self.offline_buffer), f)
            else:
                # Buffer is empty, remove the file
                self.clear_buffer_file()
        except Exception as e:
            logger.error(f"Error saving buffer to disk: {e}")
    
    def clear_buffer_file(self):
        """Remove the buffer file when no longer needed"""
        try:
            if os.path.exists(self.buffer_file):
                os.remove(self.buffer_file)
                logger.info("Cleared persistent buffer file")
        except Exception as e:
            logger.error(f"Error clearing buffer file: {e}")

    def setup_serial_connection(self):
        """Initialize serial connection to barcode scanner"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            logger.info(f"Serial connection established on {self.port}")
            return True
        except Exception as e:
            self.log_to_api(
                "Failed to establish serial connection",
                f"Port: {self.port}, Baudrate: {self.baudrate}, Error: {str(e)}"
            )
            return False
    
    def update_load_production(self, box_count, barcode_data):
        """Update load production via API with offline buffering support"""
        if self.network_available:
            success = self._send_production_data(box_count, barcode_data)
            if success:
                return True
            else:
                # Network might be down, update status
                self.network_available = False
        
        # Buffer each barcode separately for later retry
        for barcode, count in barcode_data.items():
            if barcode.startswith('9369998'):  # Only buffer valid barcodes
                buffered_entry = {
                    'type': 'production_data',
                    'box_count': count,
                    'barcode_data': {barcode: count},  # Store individual barcode
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')  # Consistent format
                }
                self.offline_buffer.append(buffered_entry)
        
        # Check if buffer needs consolidation
        if len(self.offline_buffer) >= self.max_buffer_size:
            self.consolidate_buffer()
        
        # Save buffer to disk (only after consolidation if needed)
        self.save_buffer_to_disk()
        
        logger.warning(f"Buffered production data - {box_count} boxes across {len([b for b in barcode_data.keys() if b.startswith('9369998')])} barcode types - Buffer size: {len(self.offline_buffer)}")
        return False
    
    def _send_production_data(self, box_count, barcode_data, timestamp=None):
        """Internal method to send production data to API - one API call per barcode type"""
        current_time = timestamp or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        all_successful = True
        
        for barcode, count in barcode_data.items():
            if barcode.startswith('9369998'):  # Only send valid product barcodes
                try:
                    url = f"{self.api_base_url}/api/LoadProduction/UpdateLoadProduction"
                    
                    # Array payload with single object
                    payload = [{
                        "boxCount": count,
                        "barcodeNumber": barcode,
                        "endTime": current_time
                    }]
                    
                    headers = {
                        'Content-Type': 'application/json'
                    }
                    
                    logger.info(f"Sending update for barcode {barcode}: {count} boxes")
                    logger.info(f"URL: {url}")
                    logger.info(f"Payload: {json.dumps(payload)}")
                    
                    response = requests.put(url, json=payload, headers=headers, timeout=15)
                    response.raise_for_status()
                    
                    logger.info(f"Successfully updated load production for {barcode}. Response: {response.status_code}")
                    
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to update load production for {barcode}: {e}")
                    # Log the response content if available for debugging
                    if hasattr(e, 'response') and e.response is not None:
                        logger.error(f"Response content: {e.response.text}")
                        logger.error(f"Response headers: {dict(e.response.headers)}")
                    all_successful = False
                    # Continue with other barcodes even if one fails
        
        return all_successful
    
    def read_barcode(self):
        """Read barcode from serial port"""
        try:
            if self.ser and self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8').strip()
                if line:
                    # Extract just the barcode number from scanner output
                    barcode = self.extract_barcode_from_scanner_output(line)
                    if barcode:
                        return barcode
                    else:
                        # Log unrecognized format
                        self.log_to_api(
                            "Unrecognized scanner output format",
                            f"Raw output: {line}"
                        )
        except Exception as e:
            self.log_to_api(
                "Error reading from serial port",
                f"Port: {self.port}, Error: {str(e)}"
            )
        return None
    
    def process_barcode(self, barcode):
        """Process a scanned barcode"""
        # Skip "No Read" entries - these are just sensor non-reads
        if barcode == "No Read":
            return
        
        # Check if this is a valid product barcode (starts with '9369998')
        if not self.is_valid_product_barcode(barcode):
            # Log unexpected barcode but don't count it (minimal logging)
            self.log_to_api(
                "Unexpected barcode scanned",
                f"Barcode: {barcode} (does not start with '9369998' - not a valid product barcode)"
            )
            return
        
        with self.data_lock:
            # Store the barcode with timestamp (limited deque automatically manages memory)
            timestamp = datetime.now()
            self.all_scanned_barcodes.append((barcode, timestamp))
            
            # Update counters for valid product barcodes only
            self.barcode_counts[barcode] += 1
            self.total_box_count += 1
            self.scan_count_since_gc += 1
            
            # Minimal console logging for production
            if self.total_box_count % 50 == 0:  # Log every 50 scans instead of 10
                logger.warning(f"Scanned: {self.total_box_count} total boxes, {len(self.barcode_counts)} unique barcodes")
            
            # Save data after each scan (power-loss protection)
            self.save_persistent_data()
            
            # Perform memory maintenance
            self.perform_memory_maintenance()
    
    def log_barcode_summary(self):
        """Log current barcode statistics"""
        logger.info("=== BARCODE SUMMARY ===")
        logger.info(f"Total product boxes scanned: {self.total_box_count}")
        logger.info(f"Unique product barcodes: {len(self.barcode_counts)}")
        
        for barcode, count in sorted(self.barcode_counts.items()):
            logger.info(f"  {barcode}: {count} boxes")
        logger.info("=====================")
    
    def should_update_api(self):
        """Check if it's time to update the API"""
        return time.time() - self.last_update_time >= self.update_interval
    
    def send_periodic_update(self):
        """Send periodic update to API and reset counters"""
        with self.data_lock:
            # Create a copy of current data
            barcode_data_copy = dict(self.barcode_counts)
            total_count_copy = self.total_box_count
            
            # Only proceed if we have data to send
            if total_count_copy == 0:
                self.last_update_time = time.time()
                return True
        
        # Send update to API (backend will decide which load to assign to)
        success = self.update_load_production(
            total_count_copy, 
            barcode_data_copy
        )
        
        if success:
            # Reset counters after successful API update
            with self.data_lock:
                self.barcode_counts.clear()
                self.total_box_count = 0
                self.last_update_time = time.time()
                
                # Clear persistent session data after successful send
                self.clear_persistent_data()
                
                # Clear old barcode history to save memory
                self.all_scanned_barcodes.clear()
                
                # Force garbage collection after major cleanup
                gc.collect()
            
            logger.warning(f"API update successful: Sent {total_count_copy} boxes")
        else:
            # Data was buffered, still update the time to prevent continuous retries
            self.last_update_time = time.time()
        
        return success
    
    def scanner_thread(self):
        """Main scanning thread"""
        logger.warning("Barcode scanner started - 24/7 production mode")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.running:
            try:
                # Update network status periodically
                self.update_network_status()
                
                barcode = self.read_barcode()
                if barcode:
                    self.process_barcode(barcode)
                    consecutive_errors = 0  # Reset error counter on successful read
                
                # Check if it's time for periodic update
                if self.should_update_api():
                    logger.warning("10-minute API update starting...")
                    self.send_periodic_update()
                
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
                
            except Exception as e:
                consecutive_errors += 1
                self.log_to_api(
                    "Error in scanner thread",
                    f"Error: {str(e)}, Consecutive errors: {consecutive_errors}"
                )
                
                if consecutive_errors >= max_consecutive_errors:
                    self.log_to_api(
                        "Too many consecutive scanner errors - stopping",
                        f"Reached {max_consecutive_errors} consecutive errors"
                    )
                    break
                
                time.sleep(1)  # Wait before retrying
    
    def start(self):
        """Start the barcode tracking system"""
        logger.warning("Starting Factory Barcode Tracker - 24/7 Production Mode")
        
        # Check initial network status
        self.network_available = self.check_network_connectivity()
        
        # Setup serial connection
        if not self.setup_serial_connection():
            logger.error("Could not establish serial connection. Please check scanner connection.")
            return False
        
        # Start scanning
        self.running = True
        scanner_thread = threading.Thread(target=self.scanner_thread, daemon=True)
        scanner_thread.start()
        
        # Check if running interactively (has a terminal)
        import sys
        if sys.stdin.isatty():
            # Interactive mode - show minimal commands
            logger.warning("Running in interactive mode")
            try:
                while self.running:
                    user_input = input("\nCommands: 'status', 'quit'\n> ").strip().lower()
                    
                    if user_input == 'quit':
                        break
                    elif user_input == 'status':
                        self.log_barcode_summary()
                    
            except (KeyboardInterrupt, EOFError):
                logger.warning("Received interrupt signal...")
        else:
            # Non-interactive mode (cron) - just run
            logger.warning("Running in non-interactive mode (cron/daemon)")
            try:
                while self.running:
                    time.sleep(60)  # Sleep longer to reduce CPU usage
            except KeyboardInterrupt:
                logger.warning("Received interrupt signal...")
        
        # Cleanup
        self.stop()
        return True
    
    def stop(self):
        """Stop the barcode tracking system"""
        logger.info("Stopping barcode tracker...")
        self.running = False
        
        # Send final update before shutting down (if data exists)
        if self.total_box_count > 0:
            logger.info("Sending final data update before shutdown...")
            self.send_periodic_update()
        
        # Try to send any remaining buffered data
        if self.offline_buffer:
            logger.info(f"Attempting to send {len(self.offline_buffer)} buffered entries...")
            if self.check_network_connectivity():
                self.network_available = True
                self.process_offline_buffer()
            
            if self.offline_buffer:
                logger.warning(f"{len(self.offline_buffer)} entries remain in offline buffer")
                self.log_to_api(
                    "System shutdown with buffered data",
                    f"{len(self.offline_buffer)} API calls could not be sent due to network issues"
                )
        
        # Close serial connection
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info("Serial connection closed.")
        
        logger.info("Barcode tracker stopped.")

def main():
    """Main function"""
    # Configuration - adjust these values as needed
    SERIAL_PORT = '/dev/ttyUSB0'  # Adjust based on your system
    BAUDRATE = 9600
    API_BASE_URL = 'https://goldenfries-api-6cd6d0acd119.herokuapp.com'
    
    # Create and start tracker
    tracker = BarcodeTracker(
        port=SERIAL_PORT,
        baudrate=BAUDRATE,
        api_base_url=API_BASE_URL
    )
    
    try:
        tracker.start()
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Program terminated.")

if __name__ == "__main__":
    main()