import socket
import pyvirtualcam
import threading
import time
import av
import os
import platform
import random
import customtkinter as ctk

ctk.set_appearance_mode("System")

class CamoidApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Camoid Hybrid Host")
        self.geometry("500x480")
        
        self.server_running = False
        self.stop_event = threading.Event()
        self.local_ip = "127.0.0.1"
        self.tcp_port = 8890
        self.video_port = 8888
        self.pairing_pin = ""

        # Tracking active client TCP connections
        self.client_tcp_socket = None
        self.client_address = None

        # UI Setup
        self.app_bar = ctk.CTkFrame(self, height=50, fg_color="#1e1e1e", corner_radius=0)
        self.app_bar.pack(fill="x", side="top")
        self.title_label = ctk.CTkLabel(self.app_bar, text=f"HOST: {platform.node()}", font=("Arial", 14, "bold"))
        self.title_label.place(relx=0.5, rely=0.5, anchor="center")

        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(expand=True, fill="both")

        self.status_label = ctk.CTkLabel(self.main_container, text="System Offline", font=("Arial", 20))
        self.status_label.pack(pady=10)
        self.ip_label = ctk.CTkLabel(self.main_container, text="IP: Unbound", font=("Arial", 12), text_color="gray")
        self.ip_label.pack(pady=5)
        self.pin_label = ctk.CTkLabel(self.main_container, text="PIN: ----", font=("Arial", 28, "bold"), text_color="#FFCC00")
        self.pin_label.pack(pady=15)

        self.start_button = ctk.CTkButton(self.main_container, text="START DISCOVERY", 
                                          command=self.toggle_server, fg_color="green", height=50, width=200)
        self.start_button.pack(pady=15)

    def generate_pin(self):
        return f"{random.randint(1000, 9999)}"

    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip

    def find_available_port(self, start_port, socket_type=socket.SOCK_DGRAM):
        sock = socket.socket(socket.AF_INET, socket_type)
        port = start_port
        while port < 65535:
            try:
                sock.bind((self.local_ip, port))
                sock.close()
                return port
            except OSError:
                port += 1
        return start_port

    def run_beacon(self):
        """Broadcasts the dynamic TCP port to the network"""
        beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Notice we pass self.tcp_port now!
        msg = f"CAMOID_SERVER:{platform.node()}:{self.local_ip}:{self.tcp_port}".encode('utf-8')
        
        while not self.stop_event.is_set():
            try:
                beacon_sock.sendto(msg, ('<broadcast>', 8889))
                time.sleep(2)
            except: 
                break
        beacon_sock.close()

    def tcp_command_server_loop(self):
        """Listens for safe, persistent TCP control connections from the phone"""
        server_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_tcp.bind((self.local_ip, self.tcp_port))
            server_tcp.listen(1)
            server_tcp.settimeout(1.0)
            
            while not self.stop_event.is_set():
                try:
                    conn, addr = server_tcp.accept()
                    print(f"Connected to client command channel: {addr}")
                    self.client_tcp_socket = conn
                    self.client_address = addr
                    
                    # Read pairing/control communications
                    self.handle_client_commands(conn, addr)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"TCP Accept exception: {e}")
                    break
        finally:
            server_tcp.close()

    def handle_client_commands(self, conn, addr):
        """Processes control data stream sent through the TCP pipe"""
        conn.settimeout(None) # Infinite block reading commands cleanly
        try:
            while not self.stop_event.is_set():
                data = conn.recv(1024)
                if not data:
                    break # Client disconnected abruptly
                
                message = data.decode('utf-8', errors='ignore').strip()
                print(f"TCP Command Received: {message}")
                
                if message.startswith("CAMOID_PAIR:"):
                    received_pin = message.split(":", 1)[1]
                    if received_pin == self.pairing_pin:
                        # Success! Send confirmation back via TCP alongside the dynamic UDP Port assignment
                        reply = f"CAMOID_SUCCESS:{self.video_port}\n"
                        conn.sendall(reply.encode('utf-8'))
                        
                        self.after(0, lambda: self.status_label.configure(text=f"Streaming: {addr[0]}", text_color="green"))
                    else:
                        conn.sendall(b"CAMOID_FAIL\n")
                        break

                elif message == "CAMOID_STOP":
                    print("Phone manually stopped the feed.")
                    break
                    
        except Exception as e:
            print(f"Command pipeline dropped: {e}")
        finally:
            conn.close()
            self.client_tcp_socket = None
            # Re-trigger UI fallback reset on separate thread context safely
            self.after(0, self.reset_host_state)

    def video_server_loop(self):
        """Pure UDP video receiver loop"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.local_ip, self.video_port))
            sock.settimeout(1.0)
            codec = av.CodecContext.create('h264', 'r')
            cam = None

            while not self.stop_event.is_set():
                try:
                    # Only unpack frames if a validated client address is tracked via TCP
                    data, addr = sock.recvfrom(65535)
                    if not self.client_address or addr[0] != self.client_address[0]:
                        continue

                    packets = codec.parse(data)
                    for packet in packets:
                        frames = codec.decode(packet)
                        for frame in frames:
                            img_array = frame.to_ndarray(format='rgb24')
                            if cam is None:
                                h, w, _ = img_array.shape
                                cam = pyvirtualcam.Camera(width=w, height=h, fps=30)
                            cam.send(img_array)
                            cam.sleep_until_next_frame()
                except socket.timeout: 
                    continue
        except Exception as e:
            print(f"Video pipeline crash: {e}")
        finally:
            if cam:
                cam.close()
            sock.close()

    def reset_host_state(self):
        """Safely clean states and regenerate pins on disconnects"""
        self.client_address = None
        self.pairing_pin = self.generate_pin()
        self.pin_label.configure(text=f"PAIRING PIN: {self.pairing_pin} (TCP: {self.tcp_port})")
        self.status_label.configure(text="Awaiting new connection...", text_color="orange")

    def toggle_server(self):
        if not self.server_running:
            self.server_running = True
            self.stop_event.clear()
            self.client_address = None
            self.client_tcp_socket = None
            
            self.local_ip = self.get_local_ip()
            self.tcp_port = self.find_available_port(8890, socket.SOCK_STREAM)
            self.video_port = self.find_available_port(8888, socket.SOCK_DGRAM)
            self.pairing_pin = self.generate_pin()
            
            self.ip_label.configure(text=f"Bound to Network: {self.local_ip}", text_color="cyan")
            self.pin_label.configure(text=f"PAIRING PIN: {self.pairing_pin} (TCP: {self.tcp_port})")
            self.start_button.configure(text="STOP SERVER", fg_color="red")
            self.status_label.configure(text="Enter PIN on your device...", text_color="orange")
            
            threading.Thread(target=self.run_beacon, daemon=True).start()
            threading.Thread(target=self.tcp_command_server_loop, daemon=True).start()
            threading.Thread(target=self.video_server_loop, daemon=True).start()
        else:
            # If server is stopped manually, inform the connected phone client via TCP first
            if self.client_tcp_socket:
                try:
                    self.client_tcp_socket.sendall(b"CAMOID_HOST_STOP\n")
                    self.client_tcp_socket.close()
                except:
                    pass

            self.server_running = False
            self.stop_event.set()
            self.client_address = None
            self.client_tcp_socket = None
            
            self.start_button.configure(text="START DISCOVERY", fg_color="green")
            self.status_label.configure(text="System Offline", text_color="white")
            self.ip_label.configure(text="IP: Unbound", text_color="gray")
            self.pin_label.configure(text="PIN: ----")

if __name__ == "__main__":
    app = CamoidApp()
    app.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))
    app.mainloop()