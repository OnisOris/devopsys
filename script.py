import psutil

def get_gpu_usage():
    try:
        return psutil.sensors_temperatures()['gpu']
    except KeyError:
        print("GPU usage not available")
        return None

if __name__ == "__main__":
    gpu_usage = get_gpu_usage()
    if gpu_usage is not None:
        print(f"GPU usage: {gpu_usage}")

def main() -> None:
    pass
