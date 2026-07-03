import subprocess
import sys
import time
import signal
import os

# Define the scripts and their ports
SELECTORS = [
    {"name": "Telnet", "script": "bbs_selector_telnet.py", "port": 1337},
    {"name": "SSH", "script": "bbs_selector_ssh.py", "port": 1338},
    {"name": "Rlogin", "script": "bbs_selector_rlogin.py", "port": 1339}
]

processes = []

def signal_handler(signum, frame):
    print("\nShutting down all selectors...")
    for p in processes:
        try:
            p.terminate()
        except:
            pass
    sys.exit(0)

def main():
    # Set up signal handler for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("Starting A-Net Online BBS Selectors\n")

    # Start each selector script
    for selector in SELECTORS:
        try:
            print(f"Starting {selector['name']} selector on port {selector['port']}...")
            process = subprocess.Popen([sys.executable, selector['script']], 
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            processes.append(process)
            print(f"{selector['name']} selector started successfully")
        except Exception as e:
            print(f"Error starting {selector['name']} selector: {e}")

    print("\nAll selectors started. Press Ctrl+C to shut down.\n")

    # Monitor the processes
    while True:
        for i, p in enumerate(processes):
            if p.poll() is not None:
                # Process has terminated
                selector = SELECTORS[i]
                print(f"\n{selector['name']} selector (port {selector['port']}) has stopped. Restarting...")
                try:
                    # Restart the script
                    new_process = subprocess.Popen([sys.executable, selector['script']],
                                                 stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE)
                    processes[i] = new_process
                    print(f"{selector['name']} selector restarted successfully")
                except Exception as e:
                    print(f"Error restarting {selector['name']} selector: {e}")
        
        time.sleep(5)  # Check every 5 seconds

if __name__ == "__main__":
    main()
