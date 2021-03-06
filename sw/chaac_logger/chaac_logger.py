#!/usr/bin/env python
"""
Decode incoming packets from weather station and save to db
"""

import argparse
import os
import serial
import sys
import time
from datetime import datetime
from chaac import packets
from chaac.chaacdb import ChaacDB
from serial_packet.serial_packet import decode_packet, encode_packet

parser = argparse.ArgumentParser()

parser.add_argument("--baud_rate", default=115200, type=int, help="xbee baud rate")

parser.add_argument("--port", required=True, help="xbee device to connect to")

parser.add_argument("--db", help="Sqlite db file")

args = parser.parse_args()

if args.db:
    db = ChaacDB(args.db)
else:
    db = None

stream = serial.Serial(args.port, baudrate=args.baud_rate, timeout=0.01)
stream.flushInput()


def clear_rain(uid):
    # Clear the rain count
    # This way if we miss a packet, we don't lose the rain data
    packet = packets.BootPacket.encode((uid, packets.PACKET_TYPE_CLEAR_RAIN, 0x00))
    stream.write(encode_packet(packet))


def process_data_packet(packet):
    data = packets.WeatherPacket.decode(packet)
    data = packets.WeatherPacket.round(data)
    print(data)

    if data.rain > 0:
        clear_rain(data.uid)

    if db is not None:
        db.add_record(data)


packet_processors = {
    packets.PACKET_TYPE_DATA: process_data_packet,
    # packets.PACKET_TYPE_GPS: process_gps_packet,
}


def process_packet(packet_bytes):

    try:
        header = packets.PacketHeader.decode(packet_bytes)

        if header.packet_type in packet_processors:
            packet_processors[header.packet_type](packet_bytes)
        else:
            print("ERR: Unknown type", header.packet_type)

    except UnicodeDecodeError:
        print("unicode error")
        pass


buff = bytearray()

while True:
    line = stream.read(1)
    if len(line) > 0:
        buff.append(line[0])
        while decode_packet(buff, process_packet) is True:
            pass
