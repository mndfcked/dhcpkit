# http://www.iana.org/go/rfc5908

import configparser
from ipaddress import IPv6Address
import re
from struct import unpack_from, pack

from dhcp.ipv6 import option_registry
from dhcp.ipv6.options import Option
from dhcp.utils import camelcase_to_underscore
from dhcp.ipv6 import parse_domain_name, encode_domain_name
from dhcp.parsing import StructuredElement

OPTION_NTP_SERVER = 56

NTP_SUBOPTION_SRV_ADDR = 1
NTP_SUBOPTION_MC_ADDR = 2
NTP_SUBOPTION_SRV_FQDN = 3

# Registry
# type: {int: Option}
registry = {}

# Name Registry
# type: {str: Option}
name_registry = {}


def register(subclass: type) -> None:
    """
    Register a new option type in the option registry.

    :param subclass: A subclass of Option that implements the option
    """
    if not issubclass(subclass, NTPSubOption):
        raise TypeError('Only NTPSubOptions can be registered')

    # Store based on number
    # noinspection PyUnresolvedReferences
    registry[subclass.suboption_type] = subclass

    # Store based on name
    name = subclass.__name__
    if name.startswith('NTP'):
        name = name[3:]
    if name.endswith('SubOption'):
        name = name[:-9]
    name = camelcase_to_underscore(name)
    name_registry[name] = subclass


# This subclass remains abstract
# noinspection PyAbstractClass
class NTPSubOption(StructuredElement):
    """
    https://tools.ietf.org/html/rfc5908
    """

    # This needs to be overwritten in subclasses
    suboption_type = 0

    @classmethod
    def from_string(cls, config: str) -> object:
        """
        Create this suboption based on the provided string

        :param config: The input string
        :return: The suboption object
        """
        raise configparser.Error("{} does not support loading from string".format(cls.__name__))

    @classmethod
    def determine_class(cls, buffer: bytes, offset: int=0) -> type:
        """
        Return the appropriate subclass from the registry, or UnknownNTPSubOption if no subclass is registered.

        :param buffer: The buffer to read data from
        :return: The best known class for this suboption data
        """
        suboption_type = unpack_from('!H', buffer, offset=offset)[0]
        return registry.get(suboption_type, UnknownNTPSubOption)

    def parse_suboption_header(self, buffer: bytes, offset: int=0, length: int=None) -> (int, int):
        """
        Parse the option code and length from the buffer and perform some basic validation.

        :param buffer: The buffer to read data from
        :param offset: The offset in the buffer where to start reading
        :param length: The amount of data we are allowed to read from the buffer
        :return: The number of bytes used from the buffer and the value of the suboption-len field
        """
        suboption_type, suboption_len = unpack_from('!HH', buffer, offset=offset)
        my_offset = 4

        if suboption_type != self.suboption_type:
            raise ValueError('The provided buffer does not contain {} data'.format(self.__class__.__name__))

        if length is not None and suboption_len + my_offset > length:
            raise ValueError('This suboption is longer than the available buffer')

        return my_offset, suboption_len


class UnknownNTPSubOption(NTPSubOption):
    def __init__(self, suboption_type: int=0, suboption_data: bytes=b''):
        self.suboption_type = suboption_type
        self.suboption_data = suboption_data

    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset = 0

        self.suboption_type, option_len = unpack_from('!HH', buffer, offset=offset + my_offset)
        my_offset += 4

        max_length = length or (len(buffer) - offset)
        if my_offset + option_len > max_length:
            raise ValueError('This suboption is longer than the available buffer')

        self.suboption_data = buffer[offset + my_offset:offset + my_offset + option_len]
        my_offset += option_len

        return my_offset

    def save(self) -> bytes:
        return pack('!HH', self.suboption_type, len(self.suboption_data)) + self.suboption_data


class NTPServerAddressSubOption(NTPSubOption):
    """
    https://tools.ietf.org/html/rfc5908#section-4.1

    This suboption is intended to appear inside the OPTION_NTP_SERVER
    option.  It specifies the IPv6 unicast address of an NTP server or
    SNTP server available to the client.

    The format of the NTP Server Address Suboption is:

      0                   1                   2                   3
      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |    NTP_SUBOPTION_SRV_ADDR     |        suboption-len = 16     |
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |                                                               |
     |                                                               |
     |                   IPv6 address of NTP server                  |
     |                                                               |
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

       IPv6 address of the NTP server: An IPv6 address,

       suboption-code: NTP_SUBOPTION_SRV_ADDR (1),

       suboption-len: 16.
    """

    suboption_type = NTP_SUBOPTION_SRV_ADDR

    def __init__(self, address: IPv6Address=None):
        self.address = address

    @classmethod
    def from_string(cls, config: str) -> object:
        address = IPv6Address(config)

        option = cls(address=address)
        option.validate()
        return option

    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset, suboption_len = self.parse_suboption_header(buffer, offset, length)

        if suboption_len != 16:
            raise ValueError('NTP Server Address SubOptions must have length 16')

        self.address = IPv6Address(buffer[offset + my_offset:offset + my_offset + 16])
        my_offset += 16

        return my_offset

    def save(self) -> bytes:
        buffer = bytearray()
        buffer.extend(pack('!HH', self.suboption_type, 16))
        buffer.extend(self.address.packed)
        return buffer


class NTPMulticastAddressSubOption(NTPSubOption):
    """
    https://tools.ietf.org/html/rfc5908#section-4.2

    This suboption is intended to appear inside the OPTION_NTP_SERVER
    option.  It specifies the IPv6 address of the IPv6 multicast group
    address used by NTP on the local network.

    The format of the NTP Multicast Address Suboption is:

      0                   1                   2                   3
      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |    NTP_SUBOPTION_MC_ADDR      |        suboption-len = 16     |
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |                                                               |
     |                                                               |
     |                   Multicast IPv6 address                      |
     |                                                               |
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

       Multicast IPv6 address: An IPv6 address,

       suboption-code: NTP_SUBOPTION_MC_ADDR (2),

       suboption-len: 16.
    """

    suboption_type = NTP_SUBOPTION_MC_ADDR

    def __init__(self, address: IPv6Address=None):
        self.address = address

    @classmethod
    def from_string(cls, config: str) -> object:
        address = IPv6Address(config)

        option = cls(address=address)
        option.validate()
        return option

    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset, suboption_len = self.parse_suboption_header(buffer, offset, length)

        if suboption_len != 16:
            raise ValueError('NTP Multicast Address SubOptions must have length 16')

        self.address = IPv6Address(buffer[offset + my_offset:offset + my_offset + 16])
        my_offset += 16

        return my_offset

    def save(self) -> bytes:
        buffer = bytearray()
        buffer.extend(pack('!HH', self.suboption_type, 16))
        buffer.extend(self.address.packed)
        return buffer


class NTPServerFQDNSubOption(NTPSubOption):
    """
    https://tools.ietf.org/html/rfc5908#section-4.3

    This suboption is intended to appear inside the OPTION_NTP_SERVER
    option.  It specifies the FQDN of an NTP server or SNTP server
    available to the client.

    The format of the NTP Server FQDN Suboption is:

    0                   1                   2                   3
    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |    NTP_SUBOPTION_SRV_FQDN     |         suboption-len         |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                                                               |
    |                      FQDN of NTP server                       |
    :                                                               :
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

     suboption-code: NTP_SUBOPTION_SRV_FQDN (3),

     suboption-len: Length of the included FQDN field,

     FQDN: Fully-Qualified Domain Name of the NTP server or SNTP server.
           This field MUST be encoded as described in [RFC3315],
           Section 8.  Internationalized domain names are not allowed
           in this field.
    """

    suboption_type = NTP_SUBOPTION_SRV_FQDN

    def __init__(self, fqdn: str=''):
        self.fqdn = fqdn

    @classmethod
    def from_string(cls, config: str) -> object:
        option = cls(fqdn=config)
        option.validate()
        return option

    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset, suboption_len = self.parse_suboption_header(buffer, offset, length)
        header_offset = my_offset

        # Parse the domain labels
        max_offset = suboption_len + header_offset  # The option_len field counts bytes *after* the header fields
        domain_name_len, self.fqdn = parse_domain_name(buffer, offset=offset + my_offset, length=suboption_len)
        my_offset += domain_name_len

        if my_offset != max_offset:
            raise ValueError('Option length does not match the length of the included fqdn')

        return my_offset

    def save(self) -> bytes:
        fqdn_buffer = encode_domain_name(self.fqdn)

        buffer = bytearray()
        buffer.extend(pack('!HH', self.suboption_type, len(fqdn_buffer)))
        buffer.extend(fqdn_buffer)
        return buffer


class NTPServerOption(Option):
    """
    http://tools.ietf.org/html/rfc5908#section-4

    This option serves as a container for server location information
    related to one NTP server or Simple Network Time Protocol (SNTP)
    [RFC4330] server.  This option can appear multiple times in a DHCPv6
    message.  Each instance of this option is to be considered by the NTP
    client or SNTP client as a server to include in its configuration.

    The option itself does not contain any value.  Instead, it contains
    one or several suboptions that carry NTP server or SNTP server
    location.  This option MUST include one, and only one, time source
    suboption.  The currently defined time source suboptions are
    NTP_OPTION_SRV_ADDR, NTP_OPTION_SRV_MC_ADDR, and NTP_OPTION_SRV_FQDN.
    It carries the NTP server or SNTP server location as a unicast or
    multicast IPv6 address or as an NTP server or SNTP server FQDN.  More
    time source suboptions may be defined in the future.  While the FQDN
    option offers the most deployment flexibility, resiliency as well as
    security, the IP address options are defined to cover cases where a
    DNS dependency is not desirable.

    If the NTP server or SNTP server location is an IPv6 multicast
    address, the client SHOULD use this address as an NTP multicast group
    address and listen to messages sent to this group in order to
    synchronize its clock.

    The format of the NTP Server Option is:

      0                   1                   2                   3
      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |      OPTION_NTP_SERVER        |          option-len           |
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |                         suboption-1                           |
     :                                                               :
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |                         suboption-2                           |
     :                                                               :
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     :                                                               :
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
     |                         suboption-n                           |
     :                                                               :
     +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

       option-code: OPTION_NTP_SERVER (56),

       option-len: Total length of the included suboptions.

    This document does not define any priority relationship between the
    client's embedded configuration (if any) and the NTP or SNTP servers
    discovered via this option.  In particular, the client is allowed to
    simultaneously use its own configured NTP servers or SNTP servers and
    the servers discovered via DHCP.
    """

    option_type = OPTION_NTP_SERVER

    def __init__(self, options: [NTPSubOption]=None):
        self.options = options or []
        self.validate()

    @classmethod
    def from_config_section(cls, section: configparser.SectionProxy):
        options = []

        for name, value in section.items():
            if '-' in name or '_' in name:
                suboption_name = name.replace('-', '_').lower()
            else:
                suboption_name = camelcase_to_underscore(name)

            suboption = name_registry.get(suboption_name)
            if not suboption:
                print(name_registry)
                raise configparser.ParsingError("Unknown suboption: {}".format(suboption_name))

            for suboption_value in re.split('[,\t ]+', value):
                if not suboption_value:
                    raise configparser.ParsingError("{} option has no value".format(name))

                options.append(suboption.from_string(suboption_value))

        option = cls(options=options)
        option.validate()
        return option

    def validate(self):
        # Check if all options are allowed
        self.validate_contains(self.options)

    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset, option_len = self.parse_option_header(buffer, offset, length)
        header_offset = my_offset

        # Parse the options
        max_offset = option_len + header_offset  # The option_len field counts bytes *after* the header fields
        while max_offset > my_offset:
            used_buffer, option = NTPSubOption.parse(buffer, offset=offset + my_offset)
            self.options.append(option)
            my_offset += used_buffer

        if my_offset != max_offset:
            raise ValueError('Option length does not match the combined length of the parsed suboptions')

        self.validate()

        return my_offset

    def save(self) -> bytes:
        self.validate()

        options_buffer = bytearray()
        for option in self.options:
            options_buffer.extend(option.save())

        buffer = bytearray()
        buffer.extend(pack('!HH', self.option_type, len(options_buffer)))
        buffer.extend(options_buffer)
        return buffer

# Register the classes in this file
register(NTPServerAddressSubOption)
register(NTPMulticastAddressSubOption)
register(NTPServerFQDNSubOption)

option_registry.register(NTPServerOption)

# Specify which class may occur where
NTPServerOption.add_may_contain(NTPSubOption, 1)