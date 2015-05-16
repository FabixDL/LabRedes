#!/usr/bin/python
#coding=utf-8
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.base import app_manager
from ryu.lib import mac
from ryu.ofproto import ether
from ryu.lib.packet import arp
from ryu.lib.packet import ipv4
from ryu.lib.packet import icmp
from ryu.lib.mac import haddr_to_bin
from netaddr import *
import Queue #Libreria de cola
import ipaddr
import ipaddress

class L3Switch(app_manager.RyuApp):


    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    mac_to_port = dict()          #Dictionary for basic switch
    tabla_vlan = dict()           #Dictionary that links vlan and ports
    port_ip_mac =dict()           #Dictionary for routing
    interfaces_virtuales = dict() #Dictionary that establishes the basic interfaces
                    
    ip_mac = {}       #Tabla de enrutamiento
    colaespera = []   #Queue for packets that we must route and we do not know the destination mac
    first=False #Boolean value for adding Goto
    

#-------------------------------------------------------
#Funciones de lectura desde los ficheros auxiliares:

    def lectura_vlan(self):
        infile = open('vlan.vf', 'r')
        
        for line in infile:
            v=False
            vlan=""
            port=""
            for word in line:
                if v==False:
                    if word!=" ":
                        vlan+=word
                    else:
                        v=True
                        print ("VLAN: ",vlan)
                else:
                    if word!=" ":
                        port+=word
                    else:
                        print port
                        self.tabla_vlan[int(port)]=int(vlan)
                        port=""
        infile.close()
        print self.tabla_vlan
    


    def lectura_interfaces(self):
        infile = open('interfaces.if', 'r')
        
        for line in infile:
            v=False
            m=False
            x=False
            vlan=""
            mac=""
            mask=""
            ip=""
            for word in line:
                if v==False:
                    if word!=" ":
                        vlan+=word
                    else:
                        v=True
                elif m==False:
                    if word!=" ":
                        mac+=word
                    else:
                        m=True
                elif x==False:
                    if word!=" ":
                        mask+=word
                    else:
                        x=True
                else:
                    if word!=" ":
                        ip+=word
                    else:
                        self.interfaces_virtuales[int(vlan)]=(mac,mask,ip)
                        mac=""
                        mask=""
                        ip=""
                            
        infile.close()
        print self.interfaces_virtuales
#-------------------------------------------------------

    def __init__(self, *args, **kwargs):
        super(L3Switch, self).__init__(*args, **kwargs)
        self.lectura_vlan()
        self.lectura_interfaces()
        
#------------------------------------------------------------- 
#Funciones de ayuda para operaciones repetitivas:

    def compare(self,MASK_LIST):
        #Funcion que te dice cual es la mayor mascara aplicable
        cont=0
        maximo=0
        port=0
        for MASK in MASK_LIST:
            for i in range(len(MASK[0])):
                if(MASK[0][i]=='1'):
                    cont=cont+1
            if(cont>maximo):
                maximo=cont
                port=MASK[1]
                
        return port
        
    def paraipinterfaz(self,ip):
        #Funcion para saber si un paquete ip va dirigido a las interfaces virtuales
        #y de que interfaz se trata
        adecuada=None
        for interfaz in self.interfaces_virtuales.keys():
            if self.interfaces_virtuales.get(interfaz)[2]==ip:
                adecuada=interfaz
        return adecuada
        
    def paramacinterfaz(self,mac):
        #Función para saber si la mac de un paquete va dirigida a la interfaz
        #y de que interfaz se trata
        adecuada=None
        for interfaz in self.interfaces_virtuales.keys():
            if self.interfaces_virtuales.get(interfaz)[0]==mac:
                adecuada=interfaz
        return adecuada

    def forwardPortsSameVlan(self,ofp_parser,in_port):
        #This function returns a list of ports that are in the same vlan 
        #than in_port
        lista=[]
        for j in self.tabla_vlan.keys():
            if self.tabla_vlan.get(j)==self.tabla_vlan.get(in_port) and j !=in_port:
                lista.append(ofp_parser.OFPActionOutput(j))
                print ("FORWARDING TO: ", j)
        return lista


#-------------------------------------------------------------------------------------
#Funcion para añadir un flujo a la tabla de datos
    def add_flow(self, datapath, priority, match, actions,table_id=0 ,buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                priority=priority, match=match,
                instructions=inst, idle_timeout=30,command=ofproto.OFPFC_ADD)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                    match=match,table_id=table_id, instructions=inst, 
                    idle_timeout=30,command=ofproto.OFPFC_ADD)
        print(mod)
        datapath.send_msg(mod)

    def addForwardVlanFlow(self,datapath,ofproto,ofp_parser,dst,src,port,msg):
        print("IT'S NOT FOR ME I WILL FORWARD IT THROUGH THE VLAN") 
        print("-------------------------------------------------------")
        actions = self.forwardPortsSameVlan(ofp_parser,port)  
        print("-------------------------------------------------------")
        match = ofp_parser.OFPMatch(eth_dst=dst,eth_src=src)
        inst = [ofp_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = ofp_parser.OFPFlowMod(datapath=datapath, priority=0, match=match, instructions=inst, idle_timeout=30, buffer_id=msg.buffer_id)
        datapath.send_msg(mod)      
#--------------------------------------------------------------------------------------
    def buildArpRequestPacket(self,dest_ip,source_ip,port,datapath):
        #Funcion para enviar un paquete ARP
        e = ethernet.ethernet(dst=mac.BROADCAST_STR ,
                      src=self.interfaces_virtuales.get(self.tabla_vlan[port])[0],
                      ethertype=ether.ETH_TYPE_ARP)
        a = arp.arp(opcode=arp.ARP_REQUEST,
                src_mac=self.interfaces_virtuales.get(self.tabla_vlan[port])[0], src_ip=source_ip, dst_ip=dest_ip)
        p = packet.Packet()
        p.add_protocol(e)
        p.add_protocol(a)
        
        self.sendPacket(datapath, port,p)

    def sendPacket(self, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(datapath=datapath,
                  buffer_id=ofproto.OFP_NO_BUFFER,
                  in_port=ofproto.OFPP_CONTROLLER,
                  actions=actions,
                  data=data)
        datapath.send_msg(out)

    def handleArpPacket(self,arp_msg,in_port,datapath):
        #Función que procesa los paquetes ARP que llegan a las interfaces virtuales
        if arp_msg.opcode==arp.ARP_REQUEST:
            #Si se trata de un ARP_Request para el router
            #Construir la respuesta y devolverlo por el puerto de entrada
            #Good moment for learn, we will save the port, the ip and the mac
            self.port_ip_mac[in_port]=[arp_msg.src_ip,arp_msg.src_mac]
            print("SOMEONE NEEDS KNOW ONE OF THE SVI MAC'S I WILL GIVE HIM")
            e = ethernet.ethernet(dst=arp_msg.src_mac,
                          src=self.interfaces_virtuales[self.tabla_vlan[in_port]][0],
                          ethertype=ether.ETH_TYPE_ARP)
            a = arp.arp(opcode=arp.ARP_REPLY,
                    src_mac=self.interfaces_virtuales[self.tabla_vlan[in_port]][0], src_ip=arp_msg.dst_ip,
                    dst_mac=arp_msg.src_mac, dst_ip=arp_msg.src_ip)
            p = packet.Packet()
            p.add_protocol(e)
            p.add_protocol(a)
            self.sendPacket(datapath, in_port,p)
        
        elif arp_msg.opcode==arp.ARP_REPLY:
            #If it is a reply, we must save the new info
            #and forward the waiting packets
            #Good moment for learn, we will save the port, the ip and the mac
            print("IT'S AND ARP_REPLY I WILL SAVE THIS INFO")
            print("PORT: ",in_port," SRC IP: ", arp_msg.src_ip, " MAC: ", arp_msg.src_mac)
            self.port_ip_mac[in_port]=[arp_msg.src_ip,arp_msg.src_mac]
            #Ahora revisamos si tenemos paquetes en la cola de espera que necesiten ir a esos lugares
            for paquetes in self.colaespera: #Buscamos en la lista para ver si hay paquetes en espera
                pkt_ipv4=paquetes.get_protocol(ipv4.ipv4) 
                if(pkt_ipv4):
                    if (pkt_ipv4.dst==arp_msg.src_ip): #Si la ip de destino del paquete coincide con quien envio esa ip
                        self.colaespera.remove(paquetes)
                        ofproto = datapath.ofproto
                        ofp_parser = datapath.ofproto_parser
                        actions =[ofp_parser.OFPActionSetField(eth_dst=self.port_ip_mac[in_port][1]),
                                      ofp_parser.OFPActionSetField(eth_src=self.interfaces_virtuales[self.tabla_vlan[in_port]][0]),
                                      ofp_parser.OFPActionDecNwTtl(),
                                      ofp_parser.OFPActionOutput(in_port)]
                        paquetes.serialize()
                        data = paquetes.data
                        out = ofp_parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=ofproto.OFP_NO_BUFFER,
                                  in_port=ofproto.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=data)
                        datapath.send_msg(out)
                        #Tambien debemos añadir el flujo
                        
    def handleIcmpPacket(self, datapath, in_port, pkt_ethernet, pkt_ipv4, pkt_icmp):
        #Función para procesar los paquetes ICMP que vienen a la interfaz
        if pkt_icmp.type == icmp.ICMP_ECHO_REQUEST:
            #Si es un echo request, construimos un echo reply y lo devolvemos.
            eer=ethernet.ethernet(ethertype=pkt_ethernet.ethertype,
                        dst=pkt_ethernet.src,
                        src=self.interfaces_virtuales[self.tabla_vlan[in_port]][0])
                        
            iper=ipv4.ipv4(dst=pkt_ipv4.src,
                    src=self.interfaces_virtuales[self.tabla_vlan[in_port]][2],
                    proto=pkt_ipv4.proto)
                    
            icmper=icmp.icmp(type_=icmp.ICMP_ECHO_REPLY,
                    code=icmp.ICMP_ECHO_REPLY_CODE,
                    csum=0,
                    data=pkt_icmp.data)
            p = packet.Packet()
            p.add_protocol(eer)
            p.add_protocol(iper)
            p.add_protocol(icmper)
    
            self.sendPacket(datapath, in_port, p)
              
    def paquete_para_enrutar(self, msg):
      
        datapath = msg.datapath    
        ofproto = datapath.ofproto  
        ofp_parser=datapath.ofproto_parser 

        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        pkt_ipv4=pkt.get_protocol(ipv4.ipv4)
        
        src = eth.src
        port=in_port

        listacoincide=[]
        for vlanids in self.interfaces_virtuales.keys() :
            if  ipaddr.IPv4Address(pkt_ipv4.dst) in  ipaddr.IPv4Network(self.interfaces_virtuales.get(vlanids)[2]+"/"+ self.interfaces_virtuales.get(vlanids)[1]):
                mask=IPAddress(self.interfaces_virtuales.get(vlanids)[1]).bin
                listacoincide.append((mask,vlanids))
                        
        vlanids=self.compare(listacoincide)

        yaesta=False
        for claves in self.port_ip_mac.keys():
            if(pkt_ipv4.dst == self.port_ip_mac[claves][0]):
                yaesta=True
        if(yaesta==False):
            print("DON'T KNOWN DESTINATION MAC, MAKING ARP FOR IT")
            self.colaespera.append(pkt)
            for puertos in self.tabla_vlan.keys() :
                if self.tabla_vlan.get(puertos)==vlanids:
                    self.buildArpRequestPacket(pkt_ipv4.dst,pkt_ipv4.src,puertos,datapath)
        else: #Si tenemos la mac en cache
            print("I HAVE THAT MAC IN CACHE")

            for puerto_salida in self.port_ip_mac.keys():
                if self.port_ip_mac.get(puerto_salida)[0]==pkt_ipv4.dst:
                    print("IT'S A PACKET FOR THE PORT ",puerto_salida)
                    actions = [ofp_parser.OFPActionSetField(eth_dst=self.port_ip_mac[puerto_salida][1]),
                                ofp_parser.OFPActionSetField(eth_src=self.interfaces_virtuales.get(vlanids)[0]),
                                ofp_parser.OFPActionOutput(puerto_salida)]
                                
                    match = ofp_parser.OFPMatch(ipv4_dst=pkt_ipv4.dst,eth_type=ether.ETH_TYPE_IP) #seems ok
                    inst = [ofp_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                    mod = ofp_parser.OFPFlowMod(datapath=datapath, priority=0, table_id=1, 
                    match=match, instructions=inst, buffer_id=ofproto.OFP_NO_BUFFER,command=ofproto.OFPFC_ADD)
                    print(mod)
                    datapath.send_msg(mod)
                    print("ADDING ENTRY AT FLOW TABLE 1 TO MANAGE THAT ROUTING")
                    #Example from switch 2 tables
                    #match = ofp_parser.OFPMatch(eth_dst=dst)
                    #actions = [ofp_parser.OFPActionOutput(self.mac_to_port[dst])]
                    #inst = [ofp_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                    #mod = ofp_parser.OFPFlowMod(datapath=datapath, priority=0, table_id=1, 
                    #match=match, instructions=inst, idle_timeout=40, buffer_id=msg.buffer_id)
                    # Enviamos el mensaje.
                    #datapath.send_msg(mod)

        
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg               # Objeto que representa la estuctura de datos PacketIn.
        datapath = msg.datapath    # Identificador del datapath correspondiente al switch.
        ofproto = datapath.ofproto # Protocolo utilizado que se fija en una etapa 
                                   # de negociacion entre controlador y switch

        ofp_parser=datapath.ofproto_parser # Parser con la version OF
                                           # correspondiente
        print("===========================================================================")
        print("PAQUETE ENTRANTE")
        in_port = msg.match['in_port'] # Puerto de entrada.
        print("PUERTO: ", in_port)
        # We extract the packet from the data
        pkt = packet.Packet(msg.data)
        # And we extract the ethernet protocol
        eth = pkt.get_protocol(ethernet.ethernet) 

        src = eth.src #Source MAC
        dst = eth.dst #Destiny MAC
        print("SOURCE MAC: ", src)
        print("DESTINY MAC: ", dst)
        
        if src not in self.mac_to_port.keys():
            print("I DON'T KNOW THAT MAC, SAVING MAC-PORT")
            self.mac_to_port[src]=in_port

        if haddr_to_bin(dst) == mac.BROADCAST or mac.is_multicast(haddr_to_bin(dst)):
            if eth.ethertype==ether.ETH_TYPE_ARP:
                pkt_arp=pkt.get_protocol(arp.arp)
                print("ARP CAME BY BROADCAST")
                if self.paraipinterfaz(pkt_arp.dst_ip)!=None:
                    print("IT'S FOR A SVI, WE SHOULD ANSWER THAT")
                    self.handleArpPacket(pkt_arp,in_port,datapath)
                else:
                    print("NORMAL BROADCAST")
                    #We just have to fordward the packet thourgh the ports
                    #in the same vlan as the in_port
                    self.addForwardVlanFlow(datapath,ofproto,ofp_parser,dst,src,in_port,msg)
            else: 
                print("NORMAL BROADCAST")
                #We just have to fordward the packet thourgh the ports
                #in the same vlan as the in_port 
                self.addForwardVlanFlow(datapath,ofproto,ofp_parser,dst,src,in_port,msg)
                
        else:
            interfazdestino=self.paramacinterfaz(dst)
            if interfazdestino!=None:
            #Si la mac de destino es la interfaz, tendremos que hacer otras comprobaciones
                pkt_arp=pkt.get_protocol(arp.arp)
                print("THAT MAC IS FOR ANY SVI")
                INTERFACE=interfazdestino

                if(self.interfaces_virtuales.get(INTERFACE)[0]==dst):
                    if eth.ethertype==0x0800: #Si es IP
                        pkt_ipv4=pkt.get_protocol(ipv4.ipv4)
                        if self.paraipinterfaz(pkt_ipv4.dst):
                            pkt_icmp=pkt.get_protocol(icmp.icmp)
                            if (pkt_icmp): #Si es ICMP
                                print("ESE IP ES PARA MI, RESPONDERE")
                                self.handleIcmpPacket(datapath, in_port, eth, pkt_ipv4, pkt_icmp)
                            else:
                                #Ha llegado un paquete ip no icmp a la interfaz, deberiamos tirarlo
                                actions=[]
                                match = ofp_parser.OFPMatch(eth_dst=dst,ipv4_dst=pkt_ipv4.dst,ip_proto=pkt_ipv4.ip_proto)
                                inst = [ofp_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                                mod = ofp_parser.OFPFlowMod(datapath=datapath, priority=10, match=match, 
                                                instructions=inst, buffer_id=msg.buffer_id)
                                datapath.send_msg(mod)
                        else:
                            print("THIS IS IP PACKET IS NOT FOR ME, I WILL ROUTE THAT")
                            #We add the entry to the table 0 who makes the GoTo to the table 1
                            print("ALL THIS PACKET SHOULD BE PROCESSED ON THE TABLE 1 ADDING GOTO")
                            match = ofp_parser.OFPMatch(eth_dst=self.interfaces_virtuales.get(INTERFACE)[0])
                            goto = ofp_parser.OFPInstructionGotoTable(1)
                            mod = ofp_parser.OFPFlowMod(datapath=datapath,priority=0,match=match,table_id=0,instructions=[goto],buffer_id=ofproto.OFP_NO_BUFFER,command=ofproto.OFPFC_ADD)
                            print(mod)
                            datapath.send_msg(mod)
                            #And we process the packet for routing
                            self.paquete_para_enrutar(msg)

                    elif eth.ethertype==ether.ETH_TYPE_ARP:
                        print("AN ARP FOR ME, I WILL MANAGE THAT")
                        pkt_arp=pkt.get_protocol(arp.arp)
                        self.handleArpPacket(pkt_arp,in_port,datapath)

            #If the packet isn't going to the interface
            #it might go to the same vlan, so we have to forward it      
            else:
                if dst not in self.mac_to_port.keys() :
                    print("I DON'T KNOW THAT MAC ", dst)
                    print("IN THE VLAN ", self.tabla_vlan.get(in_port), " WHO HAS MAC: ", dst,"?")
                    actions = self.forwardPortsSameVlan(ofp_parser,in_port)
                    req = ofp_parser.OFPPacketOut(datapath=datapath, buffer_id=ofproto.OFP_NO_BUFFER,
                                                  in_port=in_port, actions=actions, data=msg.data)
                    datapath.send_msg(req)
                    
                else:
                    puertodestino=self.mac_to_port[dst]
                    if self.tabla_vlan.get(self.mac_to_port.get(src)) == self.tabla_vlan.get(puertodestino) :
                        #We know the macs, and we know they are on the same vlan
                        #Direct forwarding
                        actions = [ofp_parser.OFPActionOutput(puertodestino)]
                        print("FORWARDING INSIDE THE VLAN: ", self.tabla_vlan.get(puertodestino))
                    else: 
                        #If they do not belong the same vlan
                        #and they are trying to go directly
                        #we must drop the packet this is an error
                        print("CANNOT DIRECT FORWARD THROUGH VLANS ALL PACKETS WILL BE DROPPED")
                        actions=[]
                    match = ofp_parser.OFPMatch(eth_dst=dst,eth_src=src)
                    inst = [ofp_parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                    mod = ofp_parser.OFPFlowMod(datapath=datapath, priority=0, match=match, 
                                                instructions=inst, buffer_id=msg.buffer_id)
                    datapath.send_msg(mod)

