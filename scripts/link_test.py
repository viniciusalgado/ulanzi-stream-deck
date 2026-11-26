"""Check whether the company laptop can reach the personal PC over the network.

Standard library only, so it runs on the laptop without installing anything.

  On the personal PC:   python link_test.py server
  On the laptop:        python link_test.py client 192.168.1.20

The server only answers pings; it never runs or executes anything it receives.
The laptop connects *out* to the PC, because company firewalls usually block
incoming connections but allow outgoing ones.
"""
import socket
import statistics
import sys
import time

PORT = 24801
PINGS = 50


def server() -> None:
    with socket.create_server(("0.0.0.0", PORT)) as srv:
        print(f"Listening on port {PORT}. Run the client on the laptop now (Ctrl+C to stop).")
        while True:
            conn, addr = srv.accept()
            print(f"Connected from {addr[0]}")
            with conn, conn.makefile("rwb") as f:
                for line in f:
                    f.write(line)  # echo back; the client measures the round trip
                    f.flush()
            print(f"{addr[0]} disconnected")


def client(host: str) -> None:
    print(f"Connecting to {host}:{PORT} ...")
    try:
        sock = socket.create_connection((host, PORT), timeout=5)
    except OSError as e:
        sys.exit(f"FAILED to connect: {e}\n(VPN or firewall is blocking it, or the server isn't running.)")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    rtts = []
    with sock, sock.makefile("rwb") as f:
        for i in range(PINGS):
            t = time.perf_counter()
            f.write(f"ping {i}\n".encode())
            f.flush()
            if not f.readline():
                sys.exit("Server closed the connection.")
            rtts.append((time.perf_counter() - t) * 1000)
    print(f"OK: {PINGS} round trips, median {statistics.median(rtts):.1f} ms, worst {max(rtts):.1f} ms")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "server":
        server()
    elif len(sys.argv) >= 3 and sys.argv[1] == "client":
        client(sys.argv[2])
    else:
        sys.exit(__doc__)
