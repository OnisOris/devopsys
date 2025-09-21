import subprocess
import sys

def get_gpu_utilization():
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
    except FileNotFoundError as e:
        print("Could not find nvidia-smi command. Is the NVIDIA driver installed?")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print("nvidia-smi returned error code {}: {}".format(e.returncode, e.output))
        sys.exit(1)
    
    gpu_utilization = result.stdout.splitlines()
    return [float(line.strip()) for line in gpu_utilization]

def main():
    utilization = get_gpu_utilization()
    print("GPU Utilization:")
    for i, u in enumerate(utilization):
        print(f"{i}: {u}%")

if __name__ == "__main__":
    main()
