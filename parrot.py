#!/usr/bin/env python3

import paho.mqtt.client as mqtt
from meshtastic import mesh_pb2, mqtt_pb2, portnums_pb2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64
import random
import time
import threading
import json
import signal
import sys
import google.protobuf
from google.protobuf import json_format


# Load settings from JSON file
with open('settings.json', 'r') as f:
    settings = json.load(f)

# Update variables with settings
MQTT_BROKER = settings.get('MQTT_BROKER', 'mqtt.meshtastic.org')
MQTT_PORT = settings.get('MQTT_PORT', 1883)
MQTT_USERNAME = settings.get('MQTT_USERNAME', 'meshdev')
MQTT_PASSWORD = settings.get('MQTT_PASSWORD', 'large4cats')
root_topic = settings.get('root_topic', 'msh/ANZ/2/c/')
channel = settings.get('channel', 'LongFast')
key = settings.get('key', '1PG7OiApB1nwvP+rz05pAQ==')
long_name_entry = settings.get('long_name_entry', 'MQTT-PARROT')
client_hw_model = settings.get('client_hw_model', 'PRIVATE_HW')
REPLY_DELAY = settings.get('REPLY_DELAY', 1)
NODE_INFO_PERIOD = settings.get('NODE_INFO_PERIOD', 900)
COMMAND_NODE = settings.get('COMMAND_NODE')

short_name_entry = "\U0001F99C" # 🦜 emoji

# New position settings
SEND_POSITION = settings.get('SEND_POSITION', False)
lat = settings.get('lat', '')
lon = settings.get('lon', '')
alt = settings.get('alt', '')


padded_key = key.ljust(len(key) + ((4 - (len(key) % 4)) % 4), '=')
replaced_key = padded_key.replace('-', '+').replace('_', '/')
key = replaced_key

broadcast_id = 4294967295

def create_node_id(node_number):
    return f"!{hex(node_number)[2:]}"

def decode_node_id(node_id):
    hex_string = node_id[1:]  # Removing the '!' character
    return int(hex_string, 16)

node_id = "!abcde1e2"
node_number = decode_node_id(node_id)
command_node_number = decode_node_id(COMMAND_NODE)

# node_id = create_node_id(node_number)
node_name = node_id

print(f'AUTO-ROUTER NODE-ID: {node_id}')
print(f'COMMAND NODE-ID: {COMMAND_NODE}')


def set_topic():
    global subscribe_topic, publish_topic, node_number, node_name
    node_name = '!' + hex(node_number)[2:]
    subscribe_topic = root_topic + channel + "/#"
    publish_topic = root_topic + channel + "/" + node_name

def current_time():
    current_time_seconds = time.time()
    current_time_struct = time.localtime(current_time_seconds)
    current_time_str = time.strftime("%H:%M:%S", current_time_struct)
    return(current_time_str)

def xor_hash(data):
    result = 0
    for char in data:
        result ^= char
    return result

def generate_hash(name, key):
    replaced_key = key.replace('-', '+').replace('_', '/')
    key_bytes = base64.b64decode(replaced_key.encode('utf-8'))
    h_name = xor_hash(bytes(name, 'utf-8'))
    h_key = xor_hash(key_bytes)
    result = h_name ^ h_key
    return result

def publish_message(destination_id, message):
    global key
    message_text = message
    if message_text:
        encoded_message = mesh_pb2.Data()
        encoded_message.portnum = portnums_pb2.TEXT_MESSAGE_APP 
        encoded_message.payload = message_text.encode("utf-8")

    generate_mesh_packet(destination_id, encoded_message)

def generate_mesh_packet(destination_id, encoded_message):
    mesh_packet = mesh_pb2.MeshPacket()

    setattr(mesh_packet, "from", node_number)
    mesh_packet.id = random.getrandbits(32)
    mesh_packet.to = destination_id
    mesh_packet.want_ack = False
    mesh_packet.channel = generate_hash(channel, key)
    mesh_packet.hop_limit = 3

    if key == "":
        mesh_packet.decoded.CopyFrom(encoded_message)
    else:
        mesh_packet.encrypted = encrypt_message(channel, key, mesh_packet, encoded_message)

    service_envelope = mqtt_pb2.ServiceEnvelope()
    service_envelope.packet.CopyFrom(mesh_packet)
    service_envelope.channel_id = channel
    service_envelope.gateway_id = node_name

    payload = service_envelope.SerializeToString()
    set_topic()
    client.publish(publish_topic, payload)

def encrypt_message(channel, key, mesh_packet, encoded_message):
    mesh_packet.channel = generate_hash(channel, key)
    key_bytes = base64.b64decode(key.encode('ascii'))
    nonce_packet_id = mesh_packet.id.to_bytes(8, "little")
    nonce_from_node = node_number.to_bytes(8, "little")
    nonce = nonce_packet_id + nonce_from_node

    cipher = Cipher(algorithms.AES(key_bytes), modes.CTR(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_bytes = encryptor.update(encoded_message.SerializeToString()) + encryptor.finalize()

    return encrypted_bytes

known_id_list = []

parrot_emoji = "\U0001F99C"

last_reply_timestamp = 0

def process_message(mp, text_payload, is_encrypted):
    global last_reply_timestamp
    # print(mp)
    mp_id = getattr(mp, "id")
    mp_to = getattr(mp, "to")
    mp_from = getattr(mp, "from")
    mp_timestamp = getattr(mp, "rx_time")
    # print(mp_timestamp) # time format: 1709684120

    parrot_flag = False
    broadcast_flag = False
    direct_flag = False
    from_parrot = False
    command_flag = False
    command_node_flag = False

    if mp_from == node_number:
        print("Parrot message detected")
        from_parrot = True

    if mp_id not in known_id_list:
        known_id_list.append(mp_id)
        print(mp)

        # if create_node_id(mp_to) == "!ffffffff" or create_node_id(mp_to) == node_id:
        #     with open('message_log.txt', 'a', encoding='utf-8') as file:
        #         file.write(f"{create_node_id(mp_from)} - {create_node_id(mp_to)}: {text_payload}\n")

        with open('message_log.txt', 'a', encoding='utf-8') as file:
            file.write(f"{create_node_id(mp_from)} - {create_node_id(mp_to)}: {text_payload}\n")
        

        if text_payload.startswith("\U0001F99C"):
            print("Parrot emoji detected! \U0001F99C")
            parrot_flag = True

        if text_payload.startswith("!"):
            print("Command detected!")
            command_flag = True
            # print(text_payload)
            if mp_from == command_node_number:
                print("Command node detected!")
                command_node_flag = True

        if mp_to == broadcast_id:
            print("broadcast message detected")
            broadcast_flag = True

        if create_node_id(getattr(mp, "to")) == node_id:
            direct_flag = True

        if direct_flag and not command_flag:
            if time.time() - last_reply_timestamp > REPLY_DELAY:
                time.sleep(REPLY_DELAY)
                publish_message(mp_from, f'PARROT:{text_payload}')
                last_reply_timestamp = time.time()

        if broadcast_flag and parrot_flag and not from_parrot:
            if time.time() - last_reply_timestamp > REPLY_DELAY:
                time.sleep(REPLY_DELAY)
                publish_message(broadcast_id, f'{parrot_emoji} num-num')
                last_reply_timestamp = time.time()

        if direct_flag and command_flag:
            # sendTraceRoute(mp_from, 3)
            if command_flag and not command_node_flag:
                publish_message(mp_from, f'PARROT:You are not a command node!')
                

            elif command_node_flag and text_payload == "!shutdown":
                publish_message(mp_from, f'PARROT:Shutting Down Parrot!')
                close_connection()

            elif command_node_flag and text_payload == "!status":
                publish_message(mp_from, f'PARROT:status message here')

def decode_encrypted(message_packet):
    try:
        key_bytes = base64.b64decode(key.encode('ascii'))
        nonce_packet_id = getattr(message_packet, "id").to_bytes(8, "little")
        nonce_from_node = getattr(message_packet, "from").to_bytes(8, "little")
        nonce = nonce_packet_id + nonce_from_node

        cipher = Cipher(algorithms.AES(key_bytes), modes.CTR(nonce), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted_bytes = decryptor.update(getattr(message_packet, "encrypted")) + decryptor.finalize()

        data = mesh_pb2.Data()
        data.ParseFromString(decrypted_bytes)
        message_packet.decoded.CopyFrom(data)
        
        if message_packet.decoded.portnum == portnums_pb2.TEXT_MESSAGE_APP:
            text_payload = message_packet.decoded.payload.decode("utf-8")
            is_encrypted = True
            process_message(message_packet, text_payload, is_encrypted)

    except Exception as e:
        print(e)
        print(message_packet)
        pass

def send_node_info(destination_id):
    print("Sending Node Info")
    global client_short_name, client_long_name, node_name, node_number, client_hw_model, broadcast_id

    if not client.is_connected():
        print(current_time() + " >>> Connect to a broker before sending nodeinfo")
    else:
        node_number = int(node_number)

        decoded_client_id = bytes(node_name, "utf-8")
        decoded_client_long = bytes(long_name_entry, "utf-8")
        decoded_client_short = bytes(short_name_entry, "utf-8")
        decoded_client_hw_model = client_hw_model

        user_payload = mesh_pb2.User()
        setattr(user_payload, "id", decoded_client_id)
        setattr(user_payload, "long_name", decoded_client_long)
        setattr(user_payload, "short_name", decoded_client_short)
        setattr(user_payload, "hw_model", decoded_client_hw_model)

        user_payload = user_payload.SerializeToString()

        encoded_message = mesh_pb2.Data()
        encoded_message.portnum = portnums_pb2.NODEINFO_APP
        encoded_message.payload = user_payload
        encoded_message.want_response = True  # Request NodeInfo back

        generate_mesh_packet(destination_id, encoded_message)


def send_node_position(destination_id):
    print("Sending Position Info")
    global node_number, broadcast_id

    if not client.is_connected():
        message =  current_time() + " >>> Connect to a broker before sending position"
    else:
        node_number = int(node_number)
        pos_time = int(time.time())

        latitude = float(lat)  # Convert latitude to a float
        longitude = float(lon)  # Convert longitude to a float

        latitude = latitude * 1e7
        longitude = longitude * 1e7

        latitude_i = int(latitude)
        longitude_i = int(longitude)

        position_payload = mesh_pb2.Position()
        setattr(position_payload, "latitude_i", latitude_i)
        setattr(position_payload, "longitude_i", longitude_i)
        setattr(position_payload, "altitude", 420)
        setattr(position_payload, "time", pos_time)

        position_payload = position_payload.SerializeToString()

        encoded_message = mesh_pb2.Data()
        encoded_message.portnum = portnums_pb2.POSITION_APP
        encoded_message.payload = position_payload
        encoded_message.want_response = False

        generate_mesh_packet(destination_id, encoded_message)

def sendTraceRoute(destination_id, hopLimit):
    print("Sending Trace Route!")
    route_discovery = mesh_pb2.RouteDiscovery()

    encoded_message = mesh_pb2.Data()
    encoded_message.portnum = portnums_pb2.TRACEROUTE_APP

    encoded_message.want_response = True
    generate_mesh_packet(destination_id, encoded_message)


def send_node_info_periodically():
    while True:
        send_node_info(broadcast_id)
        if SEND_POSITION:
            send_node_position(broadcast_id)
        time.sleep(NODE_INFO_PERIOD)

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print(f"Connected to {MQTT_BROKER} on topic {channel}")
        send_node_info(broadcast_id)
    else:
        print(f"Failed to connect to MQTT broker with result code {str(rc)}")

def on_message(client, userdata, msg):
    service_envelope = mqtt_pb2.ServiceEnvelope()
    
    try:
        service_envelope.ParseFromString(msg.payload)
        message_packet = service_envelope.packet
    except Exception as e:
        print(f"Error parsing message: {str(e)}")
        print(msg)
        return
    
    if message_packet.HasField("encrypted") and not message_packet.HasField("decoded"):
        decode_encrypted(message_packet)

    if message_packet.decoded.portnum == portnums_pb2.NODEINFO_APP:
        info = mesh_pb2.User()
        info.ParseFromString(message_packet.decoded.payload)
        # print(info)

    if message_packet.decoded.portnum == portnums_pb2.TRACEROUTE_APP:
        routeDiscovery = mesh_pb2.RouteDiscovery()
        routeDiscovery.ParseFromString(message_packet.decoded.payload)
        asDict = google.protobuf.json_format.MessageToDict(routeDiscovery)
        
        print("Route traced:")
        routeStr = create_node_id(getattr(message_packet, "to"))
       
        if "route" in asDict:
            for nodeNum in asDict["route"]:
                routeStr += " --> " + create_node_id(nodeNum)
        routeStr += " --> " + create_node_id(getattr(message_packet, "from"))
        print(routeStr)
        print(message_packet,file=open('route_log.txt', 'a'))
        print(routeStr, file=open('route_log.txt', 'a'))
        
def close_connection():
    print("Exiting...")
    client.disconnect()  # Disconnect from the MQTT broker
    client.loop_stop()   # Stop the MQTT client loop
    node_info_thread.stop()
    sys.exit(0)

# Define a signal handler function to handle termination signals
def signal_handler(sig, frame):
    close_connection()

# Register the signal handler for SIGINT and SIGTERM signals
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    client.on_connect = on_connect
    client.username_pw_set(username=MQTT_USERNAME, password=MQTT_PASSWORD)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)

    client.on_message = on_message

    subscribe_topic = f"{root_topic}{channel}/#"
    client.subscribe(subscribe_topic, 0)

    # Create a separate thread for sending node info periodically
    node_info_thread = threading.Thread(target=send_node_info_periodically)
    node_info_thread.daemon = True
    node_info_thread.start()

    while client.loop() == 0:
        pass
