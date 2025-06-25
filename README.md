# Barcode_Scanner
https://claude.ai/chat/c3a3d8fa-2901-49d7-b21e-774c3572eaac
# Factory Barcode Scanner System

A production-ready barcode scanning system for conveyor belt monitoring, built for 24/7 operation on Raspberry Pi. Automatically tracks boxes with barcodes starting with `9369998` and sends data to API every 10 minutes.

## 🏭 Features

- **Real-time Barcode Scanning**: Pepperl+Fuchs scanner integration via serial port
- **Smart Filtering**: Only counts valid product barcodes (starting with `9369998`)
- **API Integration**: Sends data every 10 minutes to load production API
- **Offline Protection**: Buffers data during network outages with auto-recovery
- **Power-Loss Protection**: Persists data to survive unexpected shutdowns
- **Memory Optimized**: Designed for continuous 24/7 operation (5+ days)
- **Auto-Start**: Boots automatically with system via cron job

## 📋 Requirements

### Hardware
- **Raspberry Pi** (tested on Pi 4)
- **Pepperl+Fuchs Barcode Scanner** (or compatible serial scanner)
- **USB-to-Serial adapter** (if needed)
- **Network connection** (WiFi or Ethernet)

### Software
- **Python 3.7+**
- **Raspberry Pi OS** (Debian-based)

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone <repository-url>
cd factory-barcode-scanner
```

### 2. Install Dependencies
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python packages
pip3 install pyserial requests

# Or install system-wide
sudo apt install python3-serial python3-requests
```

### 3. Hardware Setup
```bash
# Connect barcode scanner to USB port
# Check if scanner is detected
lsusb

# Find serial port
ls /dev/ttyUSB*
# Usually: /dev/ttyUSB0
```

### 4. Configuration
Edit the configuration in `Barcode.py` if needed:
```python
# Line ~720 in main() function
SERIAL_PORT = '/dev/ttyUSB0'  # Adjust if different
BAUDRATE = 9600               # Scanner baud rate
API_BASE_URL = 'https://goldenfries-api-6cd6d0acd119.herokuapp.com'
```

### 5. Test Run
```bash
# Test manually first
cd /path/to/script
python3 Barcode.py

# Should show:
# "Starting Factory Barcode Tracker - 24/7 Production Mode"
# Press Ctrl+C to stop
```

### 6. Setup Auto-Start
```bash
# Edit cron job
crontab -e

# Add this line:
@reboot sleep 30 && cd /home/robotics/Desktop && nohup python3 Barcode.py </dev/null >/dev/null 2>&1 &

# Save and exit
```

### 7. Reboot and Verify
```bash
# Reboot system
sudo reboot

# After reboot, check if running
ps aux | grep Barcode.py
# Should show a running Python process
```

## 🔧 Usage

### Scanner Operation
1. **Hold MODE button** until TEST LED lights up
2. **Release button** (laser should turn on)
3. **Scan barcodes** as boxes pass on conveyor
4. System automatically processes and sends data

### Manual Commands (Interactive Mode)
When running manually, these commands are available:
- `status` - Show current scan statistics
- `quit` - Exit safely

### Monitoring
```bash
# Check if scanner is running
ps aux | grep Barcode.py

# Check memory usage
ps -p $(pgrep -f Barcode.py) -o pid,vsz,rss,pmem,comm

# Check log files (if any exist)
ls -la /home/robotics/barcode*.json
```

## 📊 Data Flow

### Normal Operation
1. **Barcode scanned** → Validated (must start with `9369998`)
2. **Data accumulated** → Saved to persistent storage
3. **Every 10 minutes** → Send to API endpoint
4. **API success** → Clear local data and start fresh

### Network Outage
1. **API calls fail** → Data buffered locally
2. **Continue scanning** → Buffer grows (with smart consolidation)
3. **Network returns** → Automatically send all buffered data
4. **Recovery complete** → Resume normal operation

### Power Loss
1. **Power cuts** → Data saved to `/home/robotics/current_barcode_data.json`
2. **System reboots** → Auto-start via cron
3. **Data restored** → Continue from where it left off
4. **Send to API** → Clean up files

## 🛡️ Data Protection

### Files Created (Temporary)
- `/home/robotics/current_barcode_data.json` - Current unsent scan data
- `/home/robotics/barcode_buffer.json` - Failed API calls buffer

### Auto-Cleanup
- Files **automatically deleted** after successful API sends
- **No permanent storage** unless there's unsent data
- **Memory optimized** for 24/7 operation

## 🔌 API Integration

### Endpoint
```
PUT https://goldenfries-api-6cd6d0acd119.herokuapp.com/api/LoadProduction/UpdateLoadProduction
```

### Request Format
```json
[
  {
    "boxCount": 15,
    "barcodeNumber": "9369998047680",
    "endTime": "2025-06-22T10:30:00.000Z"
  }
]
```

### Multiple Barcodes
Separate API calls are made for each unique barcode in the 10-minute window.

## 🐛 Troubleshooting

### Scanner Not Detected
```bash
# Check USB connection
lsusb

# Check serial ports
ls /dev/ttyUSB*

# Check permissions
sudo usermod -a -G dialout $USER
# Then logout and login again
```

### Script Won't Start
```bash
# Check Python dependencies
python3 -c "import serial, requests; print('OK')"

# Check file permissions
chmod +x Barcode.py

# Run with error output
python3 Barcode.py 2>&1 | head -20
```

### Network Issues
```bash
# Test internet connectivity
ping google.com

# Test API endpoint
curl -I https://goldenfries-api-6cd6d0acd119.herokuapp.com

# Check if script is buffering data
ls -la /home/robotics/barcode*.json
```

### Memory Issues (Long Running)
```bash
# Check memory usage
free -h

# Check process memory
ps aux --sort=-%mem | head

# Restart scanner if needed
sudo pkill -f Barcode.py
# Will auto-restart on next reboot
```

## 📁 File Structure

```
factory-barcode-scanner/
├── Barcode.py              # Main application
├── README.md               # This file
├── requirements.txt        # Python dependencies (optional)
└── docs/                   # Additional documentation
    ├── API.md              # API documentation
    ├── HARDWARE.md         # Hardware setup guide
    └── TROUBLESHOOTING.md  # Detailed troubleshooting
```

## ⚙️ Advanced Configuration

### Change Update Interval
```python
# In Barcode.py, line ~51
self.update_interval = 10 * 60  # 10 minutes (change as needed)
```

### Change Barcode Filter
```python
# In is_valid_product_barcode() method
return barcode.startswith('9369998')  # Change prefix as needed
```

### Adjust Memory Limits
```python
# In __init__ method
self.all_scanned_barcodes = deque(maxlen=100)  # Scan history limit
self.max_buffer_size = 500  # Buffer consolidation trigger
```

## 🔒 Security Notes

- **No sensitive data** stored locally
- **Auto-cleanup** prevents data accumulation
- **Network-only logging** (no local log files)
- **Minimal attack surface** (production hardened)

## 📞 Support

### Common Issues
1. **Scanner not responding** → Check USB connection and permissions
2. **API calls failing** → Verify network and API endpoint
3. **High memory usage** → System auto-manages, restart if needed
4. **Data not sending** → Check network connectivity

### System Requirements
- **RAM**: 512MB minimum (system uses <50MB)
- **Storage**: 1GB free space (for OS, <1MB for application)
- **Network**: Any internet connection (WiFi/Ethernet)
- **Power**: Stable 5V supply (UPS recommended for production)

## 📝 License

[Add your license information here]

## 🤝 Contributing

[Add contributing guidelines here]

---

**Built for Golden Fries Factory Production Line**  
*Reliable 24/7 barcode tracking for industrial conveyor systems*