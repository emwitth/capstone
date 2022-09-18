#!/usr/bin/python3

# python and third-party modules
import netifaces
from scapy.all import *
from socket import gethostbyaddr
from psutil import net_connections, Process
from datetime import datetime

# my modules
from constants import *
from interfaces.ip_interfaces import IPNode, IPNodeConnection
from interfaces.program import ProgNode, ProgInfo

PRINT_PACKET_INFO = False

emptyProcess: ProgInfo
seen_ips = {}
seen_procs = {}
my_ip = ""

# items to become the JSON object
prog_nodes = {}
ip_nodes = {}

def main():
    global emptyProcess
    emptyProcess = ProgInfo(NO_PORT, NO_PROC)
    prog_nodes[emptyProcess] = ProgNode(emptyProcess, NO_IP, NO_ROLE)
    # get my address
    getMyAddr()
    # sniff
    sniff_packets()

def getMyAddr():
    global my_ip
    for iface in netifaces.interfaces():
        iface_details = netifaces.ifaddresses(iface)
        if netifaces.AF_INET in iface_details:
            for ip_interfaces in iface_details[netifaces.AF_INET]:
                for key, ip_add in ip_interfaces.items():
                    if key == 'addr' and ip_add != '127.0.0.1':
                        my_ip = ip_add;
                        print(my_ip)
                        seen_ips[ip_add] = 'localhost'

def reverse_ip_lookup(address):
    # either I've seen this before
    if address in seen_ips:
        return seen_ips[address]
    else:
        # or I have to look it up
        try:
            host_tuple = gethostbyaddr(address)
            seen_ips[address] = host_tuple[0]
            return host_tuple[0]
        except socket.herror:
            seen_ips[address] = NO_HOSTNAME
            return NO_HOSTNAME

def check_if_src_or_dest(src, dest):
    if src == my_ip:
        return SRC
    elif dest == my_ip:
        return DEST

def associate_port_with_process(socket) -> ProgInfo:
    process_and_timestamp = "";
    # search for socket in current connections
    for connection in net_connections():
        if connection.laddr.port == socket:
            # update info if is in seen_procs, else make new info class
            if socket in seen_procs:
                seen_procs[socket].update_timestamp()
            else:
                process = ProgInfo(socket, Process(connection.pid).name())
                seen_procs[socket] = process
            return seen_procs[socket]
    # if the loop fails to find the socket, the socket is no longer being used
    # return the last associated process, or nothing if there is none
    if process_and_timestamp == "":
        if socket in seen_procs:
            return seen_procs[socket]
        else:
            return ProfInfo(socket, NO_PROC)
    return ProfInfo(socket, NO_PROC)

def update_node_info(src, dest, role, src_name, dest_name, process):
    # decide where I am src or dest and set appropriately
    if role == SRC:
        me = src
        them = dest
        my_name = src_name
        their_name = dest_name
    else:
        me = dest
        them = src
        my_name = dest_name
        their_name = src_name
    # handle case where there is no associated process
    global emptyProcess
    if process.name == NO_PROC:
        if emptyProcess in prog_nodes:
            prog_nodes[emptyProcess].updateInfo(them, their_name, role)
        return
    # if I've seen process before, have to update
    # else, make a new one
    if process in prog_nodes:
        prog_nodes[process].updateInfo(them, their_name, role)
    else:
        prog_nodes[process] = ProgNode(process, them, role)

def process_packet(packet):
    if PRINT_PACKET_INFO:
        print(LINE)
        # the summary of packets
        print(packet.summary())
    # parse the source and destination of IP packets
    packet_role = NO_ROLE
    src_ip = NO_IP
    dest_ip = NO_IP
    src_hostname = NO_HOSTNAME
    dest_hostname = NO_HOSTNAME
    if IP in packet:
        src_ip = packet[IP].src
        dest_ip = packet[IP].dst
        packet_role = check_if_src_or_dest(src_ip, dest_ip)
        if PRINT_PACKET_INFO :
            print("src: ", src_ip, reverse_ip_lookup(src_ip))
            print("dest: ", dest_ip, reverse_ip_lookup(dest_ip))
    # parse the process associated with the packet
    port = NO_PORT
    process = ProgInfo(NO_PORT, NO_PROC)
    if TCP in packet:
        if packet_role == SRC:
            port = packet[TCP].sport
        elif packet_role == DEST:
            port = packet[TCP].dport
        process = associate_port_with_process(port)
        if PRINT_PACKET_INFO:
            print("I am a packet with a {} associated with {}".format
            (
            packet_role, process
            ))
    update_node_info(src_ip, dest_ip, packet_role, src_hostname, dest_hostname, process);

def sniff_packets():
    # runs until killed
    capture = sniff(prn=process_packet)
    # print(capture.summary())
    for prog in prog_nodes:
        print(prog_nodes[prog].print_info())

if __name__ == "__main__":
    main()
