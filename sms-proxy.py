################################################################################
#
# Copyright (c) 2013 Ivan Kluchnikov (kluchnikovi@gmail.com)
#
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51 Franklin
# Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
################################################################################

import sys
import string
import random
import binascii
import sip_parser
from twisted.internet import defer
from twisted.internet import protocol
from twisted.python import log
from twisted.protocols import sip
from smspdu import *

class SIPProxyClientProtocol(sip.Base):
    
    dest_ip = "127.0.0.1"
    dest_port = "5062"
    src_ip = "127.0.0.1"
    src_port = 5063

    def __init__(self, sms_queue, sip_queue):
        self.sms_queue = sms_queue
        self.sip_queue = sip_queue

    def __gen_string(self, length):
        return ''.join(random.choice(string.letters) for i in xrange(length))

    def startProtocol(self):
        self.sip_queue.get().addCallback(self.smsServerDataReceived)

    def smsServerDataReceived(self, sms):
        destURL = sip.URL(self.dest_ip, self.dest_port)
        r = sip.Request('MESSAGE', sip.URL(self.dest_ip, sms.recipient))
        r.body = sms.user_data
        r.addHeader('via', sip.Via(self.dest_ip).toString())
        r.addHeader('from', "%s <sip:%s@%s:%s>;tag=%s" % (sms.sender, sms.sender, self.src_ip, self.dest_port, self.__gen_string(6)))
        r.addHeader('to', "%s <sip:%s@%s>" % (sms.recipient, sms.recipient, self.dest_ip))
        r.addHeader('call-id', (random.randint(100000, 10000000)))
        r.addHeader('cseq', "18 MESSAGE")
        r.addHeader('content-type', "text/plain")
        r.addHeader('content-length', len(r.body))
        self.transport.write(r.toString(), (destURL.host, self.src_port))
        log.msg("\n[SIP:TX] [Proxy =================> SMSC]\n%s" % r.toString())
        self.sip_queue.get().addCallback(self.smsServerDataReceived)

    def datagramReceived(self, data, (host, port)):
        log.msg("\n[SIP:RX] [Proxy <================ SMSC]\n%s" % data)
        msgType, firstLine, headers, body = sip_parser.parseSipMessage(data);
        if msgType == "MESSAGE":
           hdr_from = headers["from"].split()
           hdr_to = headers["to"].split()
           sms = SMS_DELIVER.create(hdr_from[0], hdr_to[0], body)
           log.msg("Create SMS_DELIVER:\n%s" % sms.dump())
           self.sms_queue.put(sms)
           # Send 200 OK to SMSC
           r = sip.Response(200, "OK")
           r.addHeader('via', headers["via"])
           r.addHeader('from', headers["from"])
           r.addHeader('to', headers["to"])
           r.addHeader('call-id', headers["call-id"])
           r.addHeader('cseq', headers["cseq"])
           r.addHeader("Content-Length", "0")
           destURL = sip.URL(self.dest_ip, self.dest_port)
           self.transport.write(r.toString(), (destURL.host, self.src_port))
           log.msg("\n[SIP:TX] [Proxy ================> SMSC]\n%s" % r.toString())
           


class SMSProxyServer(protocol.Protocol):
    
    def connectionMade(self):
        self.sms_queue = defer.DeferredQueue()
        self.sip_queue = defer.DeferredQueue()
        self.sms_queue.get().addCallback(self.sipClientDataReceived)
        self.sip_client_factory = SIPProxyClientProtocol(self.sms_queue, self.sip_queue)
        from twisted.internet import reactor
        reactor.listenUDP(5062, self.sip_client_factory)

    def sipClientDataReceived(self, sms):
        hex_sms = sms.toPDU()
        msg = sms.recipient.encode('hex')
        addr_len = 30 - len(msg)
        while addr_len > 0:
          msg = msg + '0'
          addr_len = addr_len - 1
        msg = msg + hex_sms
        data = binascii.a2b_hex(msg)
        self.transport.write(data)
        log.msg("\n[SMS:TX] [Proxy ===============> BSC]\n%s" % sms.dump())
        self.sms_queue.get().addCallback(self.sipClientDataReceived)

    def dataReceived(self, data):
        data_hex = binascii.b2a_hex(data)
        data_received = data_hex.strip()
        originator = data_received[:30].decode('hex')
        sms = SMS_SUBMIT.fromPDU(data_received[30:], originator.rstrip(' \t\r\n\0'))
        log.msg("\n[SMS:RX] [Proxy <=============== BSC]\n%s" % sms.dump())
        self.sip_queue.put(sms)

    def connectionLost(self, why):
        self.sip_queue.put(False)


class SMSProxyServerFactory(protocol.ClientFactory):

    protocol = SMSProxyServer

if __name__ == "__main__":
    log.startLogging(sys.stdout)
    factory = SMSProxyServerFactory()
    from twisted.internet import reactor
    reactor.connectUNIX("/tmp/bsc_sms", factory)
    reactor.run()
