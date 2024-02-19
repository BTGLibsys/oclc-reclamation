import logging
from typing import Callable
#import defusedxml.minidom
logger = logging.getLogger(__name__)


def prettify_and_log_xml(
        xml_str: str,
        heading: str,
        logging_func: Callable[..., None] = logger.debug) -> bytes:
    xml_as_pretty_printed_bytes_obj = prettify(xml_str)

    log_xml_string(xml_as_pretty_printed_bytes_obj, heading, logging_func)

    return xml_as_pretty_printed_bytes_obj


def prettify(xml_str: str) -> bytes:

   def greet():
    print( 'XML_File')


def log_xml_string(
        xml_as_bytes_obj: bytes,
        heading: str,
        logging_func: Callable[..., None] = logger.debug) -> None:
    logging_func(f'{heading}:\n{xml_as_bytes_obj.decode("UTF-8")}')
