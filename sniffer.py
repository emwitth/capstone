#!/usr/bin/python3

# python and third-party modules
import netifaces
from scapy.all import *
from socket import gethostbyaddr
from psutil import net_connections, Process
from threading import Lock
from datetime import datetime
from sys import stdin
from json import dumps

# my modules
from constants import *
from data_structures.node import ProgNode, ProgInfo, IPNode
from data_structures.packet import PacketInfo

class PacketSniffer:
    emptyProcess: ProgInfo
    seen_ips = {}
    ip_nodes = {}
    proc_nodes = {}
    my_ip = ""
    capture: AsyncSniffer
    lock: Lock

    # items to become the JSON object
    prog_nodes = {}
    ip_nodes = {}

    # data structures for hiding nodes
    hidden_prog_nodes = {}
    hidden_ip_nodes = {}
    hidden_links = {}

    def __init__(self):
        # add the catchall node for "no process"
        self.emptyProcess = ProgInfo(NO_PROC, NO_PORT, NO_PROC)
        self.prog_nodes[self.emptyProcess] = ProgNode(self.emptyProcess, NO_IP, NO_ROLE)
        self.capture = AsyncSniffer(prn=self.process_packet)
        self.lock = Lock()

        # get my address
        self.getMyAddr()

    def getMyAddr(self):
        for iface in netifaces.interfaces():
            iface_details = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in iface_details:
                for ip_interfaces in iface_details[netifaces.AF_INET]:
                    for key, ip_add in ip_interfaces.items():
                        if key == 'addr' and ip_add != '127.0.0.1':
                            self.my_ip = ip_add;
                            print(self.my_ip)
                            self.seen_ips[ip_add] = 'localhost'

    def reverse_ip_lookup(self, address):
        # either I've seen this before
        if address in self.seen_ips:
            return self.seen_ips[address]
        else:
            # or I have to look it up
            try:
                host_tuple = gethostbyaddr(address)
                self.seen_ips[address] = host_tuple[0]
                return host_tuple[0]
            except socket.herror:
                self.seen_ips[address] = NO_HOSTNAME
                return NO_HOSTNAME

    def check_if_src_or_dest(self, src, dest):
        if src == self.my_ip:
            return SRC
        elif dest == self.my_ip:
            return DEST

    def associate_port_with_process(self, socket) -> ProgInfo:
        process_and_timestamp = "";
        toReturn = ProgInfo(NO_PROC, socket, NO_PROC)
        # search for socket in current connections
        for connection in net_connections():
            if connection.laddr.port == socket:
                # update info if is in proc_nodes, else make new info class
                self.lock.acquire() # acquire lock
                try:
                    if socket in self.proc_nodes:
                        self.proc_nodes[socket].update_timestamp()
                    else:
                        process = ProgInfo(Process(connection.pid).name(), socket, connection.pid)
                        self.proc_nodes[socket] = process
                    toReturn = self.proc_nodes[socket]
                finally:
                    self.lock.release() # release lock
                return toReturn
        # if the loop fails to find the socket, the socket is no longer being used
        # return the last associated process, or nothing if there is none
        if process_and_timestamp == "":
            self.lock.acquire() # acquire lock
            try:
                if socket in self.proc_nodes:
                    toReturn = self.proc_nodes[socket]
            finally:
                self.lock.release() # release lock
        return toReturn

    def update_node_info(self, src, dest, role, src_name, dest_name, process, packet):
        # decide where I am src or dest and set appropriately
        if role == SRC:
            their_ip = dest
            their_name = dest_name
        else:
            their_ip = src
            their_name = src_name
        # create packet info object to be stored
        packetInfo = PacketInfo(packet.summary(),
            src, src_name,
            dest, dest_name,
            process.socket,
            packet
            )
        self.lock.acquire() # acquire lock
        try:
            # if I've seen process before, get the node
            # else, make a new one
            progNode: ProgNode
            if process in self.prog_nodes:
                progNode = self.prog_nodes[process]
            else:

                progNode = ProgNode(process, their_ip, role)
                self.prog_nodes[process] = progNode

            # if I've seen ip before, get the node
            # else, make a new one
            ipNode: IPNode
            if their_ip in self.ip_nodes:
                ipNode = self.ip_nodes[their_ip]
            else:
                ipNode = IPNode(their_ip, their_name)
                self.ip_nodes[their_ip] = ipNode

            # update nodes
            self.prog_nodes[process].update(ipNode, progNode, role, packetInfo)
            self.ip_nodes[their_ip].update(ipNode, progNode, role, packetInfo)

        finally:
            self.lock.release() # release lock

    def get_graph_json(self):
        links = []
        ips = []
        progs = []
        self.lock.acquire() # acquire lock
        try:
            for prog in self.prog_nodes.values():
                if prog.is_hidden == False:
                    links.extend(prog.make_con_list())
                    progs.append(prog.return_fields_for_json())
            for ip in self.ip_nodes.values():
                if ip.is_hidden == False:
                    ips.append(ip.get_info())
        finally:
            self.lock.release() # release lock
        print(progs)
        return {
        "links": links,
        "ip_nodes": ips,
        "prog_nodes": progs
        }

    def get_ip_node_packets(self, ip):
        packets = []
        links = []
        self.lock.acquire() # acquire lock
        print("IP---------------------")
        print(ip)
        try:
            if ip in self.ip_nodes:
                print("inside")
                node = self.ip_nodes[ip]
                for packet in node.packets:
                    packets.append(packet.get_info())
            for prog in self.prog_nodes.values():
                links.extend(prog.get_con_with_ip(ip))
        finally:
            self.lock.release() # release lock
        return {
            "packets": packets,
            "links": links
        }

    def get_prog_node_packets(self, name, socket, fd):
        progInfo = ProgInfo(name, socket, fd)
        packets = []
        links = []
        self.lock.acquire() # acquire lock
        try:
            if progInfo in self.prog_nodes:
                node = self.prog_nodes[progInfo]
                for packet in node.packets:
                    packets.append(packet.get_info())
                links.extend(node.make_con_list())
        finally:
            self.lock.release() # release lock
        return {
            "packets": packets,
            "links": links
        }

    def get_link_packets(self, ip, name, socket, fd):
        progInfo = ProgInfo(name, socket, fd)
        packets = []
        self.lock.acquire() # acquire lock
        try:
            if progInfo in self.prog_nodes:
                node = self.prog_nodes[progInfo]
                for packet in node.packets:
                    if (packet.src == ip) or (packet.dest == ip) :
                        packets.append(packet.get_info())
        finally:
            self.lock.release() # release lock
        return packets;

    def hide_prog_node(self, name, socket, fd):
        progInfo = ProgInfo(name, socket, fd)
        self.lock.acquire() # acquire lock
        try:
            if progInfo in self.prog_nodes:
                progNode = self.prog_nodes[progInfo]
                self.hidden_prog_nodes[progInfo] = progNode
                progNode.is_hidden = True
                for link in progNode.cons:
                    con = progNode.cons[link]
                    self.hidden_links[link] = con
                    con.is_hidden = True
                    con.ip.cons[link].is_hidden = True
                    if len(con.ip.cons) == 1: # this is the only connection
                        con.ip.is_hidden = True
                        self.hidden_ip_nodes[con.ip.ip] = con.ip
        finally:
            self.lock.release() # release lock

    def hide_ip_node(self, ip):
        self.lock.acquire() # acquire lock
        try:
            if ip in self.ip_nodes:
                ipNode = self.ip_nodes[ip]
                self.hidden_ip_nodes[ip] = ipNode
                ipNode.is_hidden = True
                for link in ipNode.cons:
                    con = ipNode.cons[link]
                    self.hidden_links[link] = con
                    con.is_hidden = True
                    con.program.cons[link].is_hidden = True
                    if len(con.program.cons) == 1: # this is the only connection
                        con.program.is_hidden = True
                        self.hidden_prog_nodes[con.program.program] = con.program

        finally:
            self.lock.release() # release lock

    def process_packet(self, packet):
        # variables 'global' to this function so I can use them outside of if
        packet_role = NO_ROLE
        src_ip = NO_IP
        dest_ip = NO_IP
        src_hostname = NO_HOSTNAME
        dest_hostname = NO_HOSTNAME
        port = NO_PORT
        process = ProgInfo(NO_PROC, NO_PORT, NO_PROC)
        # print scapy's summary of the packet
        if PRINT_PACKET_INFO:
            print(LINE)
            # the summary of packets
            print(packet.summary())
        # parse the source and destination of IP packets
        if IP in packet:
            src_ip = packet[IP].src
            dest_ip = packet[IP].dst
            packet_role = self.check_if_src_or_dest(src_ip, dest_ip)
            src_hostname = self.reverse_ip_lookup(src_ip)
            dest_hostname = self.reverse_ip_lookup(dest_ip)
            if PRINT_PACKET_INFO :
                print("src: ", src_ip, src_hostname)
                print("dest: ", dest_ip, dest_hostname)
        # parse the source and destination of IPv6 packets
        if IPv6 in packet:
            src_ip = packet[IPv6].src
            dest_ip = packet[IPv6].dst
            src_hostname = self.reverse_ip_lookup(src_ip)
            dest_hostname = self.reverse_ip_lookup(dest_ip)
            if PRINT_PACKET_INFO :
                print("src: ", src_ip, src_hostname)
                print("dest: ", dest_ip, dest_hostname)
        # parse the process associated with the packet
        if TCP in packet:
            if packet_role == SRC:
                port = packet[TCP].sport
            elif packet_role == DEST:
                port = packet[TCP].dport
            # should return a ProgInfo containing the info needed
            process = self.associate_port_with_process(port)
            if PRINT_PACKET_INFO:
                print("I am a packet with a {} associated with {}".format
                (
                packet_role, process
                ))
        # update count we have stored to send to frontend
        self.update_node_info(src_ip, dest_ip, packet_role,
                        src_hostname, dest_hostname, process, packet);

    def sniff_packets(self):
        print("Sniffing Started")
        self.capture.start()

    def stop_sniffing(self):
        self.capture.stop()
        print("Done Sniffing")
        for prog in self.prog_nodes:
            self.prog_nodes[prog].print_info()
        for ip in self.ip_nodes:
            self.ip_nodes[ip].print_info()
