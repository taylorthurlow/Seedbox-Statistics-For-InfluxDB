import configparser
import os
import sys
import argparse
import time
import logging
import re
import socket

from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError


# TODO Move urlopen login in each method call to one central method
# TODO Validate that we get a valid URL from config
# TODO Deal with multiple trackers per torrent

__author__ = 'barry'
class configManager():

    #TODO Validate given client url

    def __init__(self, silent, config):

        self.valid_torrent_clients = ['deluge', 'utorrent', 'rtorrent']
        self.valid_log_levels = {
            'DEBUG': 0,
            'INFO': 1,
            'WARNING': 2,
            'ERROR': 3,
            'CRITICAL': 4
        }
        self.silent = silent

        if not self.silent:
            print('Loading Configuration File {}'.format(config))

        config_file = os.path.join(os.getcwd(), config)
        if os.path.isfile(config_file):
            self.config = configparser.ConfigParser()
            self.config.read(config_file)
        else:
            print('ERROR: Unable To Load Config File: {}'.format(config_file))
            sys.exit(1)

        self._load_config_values()
        self._validate_logging_level()
        self._validate_torrent_client()
        if not self.silent:
            print('Configuration Successfully Loaded')

    def _load_config_values(self):

        # General
        self.delay = self.config['GENERAL'].getint('Delay', fallback=2)
        self.output = self.config['GENERAL'].getboolean('Output', fallback=True)
        self.hostname = self.config['GENERAL'].get('Hostname')
        if not self.hostname:
            self.hostname = socket.gethostname()


        # InfluxDB
        self.influx_address = self.config['INFLUXDB']['Address']
        self.influx_port = self.config['INFLUXDB'].getint('Port', fallback=8086)
        self.influx_database = self.config['INFLUXDB'].get('Database', fallback='speedtests')
        self.influx_user = self.config['INFLUXDB'].get('Username', fallback='')
        self.influx_password = self.config['INFLUXDB'].get('Password', fallback='')
        self.influx_ssl = self.config['INFLUXDB'].getboolean('SSL', fallback=False)
        self.influx_verify_ssl = self.config['INFLUXDB'].getboolean('Verify_SSL', fallback=True)

        #Logging
        self.logging = self.config['LOGGING'].getboolean('Enable', fallback=False)
        self.logging_level = self.config['LOGGING']['Level'].upper()
        self.logging_file = self.config['LOGGING']['LogFile']
        self.logging_censor = self.config['LOGGING'].getboolean('CensorLogs', fallback=True)
        self.logging_print_threshold = self.config['LOGGING'].getint('PrintThreshold', fallback=2)

        # TorrentClient
        self.tor_client = self.config['TORRENTCLIENT'].get('Client', fallback=None).lower()
        self.tor_client_user = self.config['TORRENTCLIENT'].get('Username', fallback=None)
        self.tor_client_password = self.config['TORRENTCLIENT'].get('Password', fallback=None)
        self.tor_client_url = self.config['TORRENTCLIENT'].get('Url', fallback=None)

    def _validate_torrent_client(self):

        if self.tor_client not in self.valid_torrent_clients:
            print('ERROR: {} Is Not a Valid or Support Torrent Client.  Aborting'.format(self.tor_client))
            sys.exit(1)

    def _validate_logging_level(self):
        """
        Make sure we get a valid logging level
        :return:
        """


        if self.logging_level in self.valid_log_levels:
            self.logging_level = self.logging_level.upper()
            return
        else:
            if not self.silent:
                print('Invalid logging level provided. {}'.format(self.logging_level))
                print('Logging will be disabled')
            self.logging = None


class influxdbSeedbox():

    def __init__(self, config=None, silent=None):

        self.config = configManager(silent, config=config)

        self.output = self.config.output
        self.logger = None
        self.delay = self.config.delay

        self.influx_client = InfluxDBClient(
            self.config.influx_address,
            self.config.influx_port,
            database=self.config.influx_database,
            ssl=self.config.influx_ssl,
            verify_ssl=self.config.influx_verify_ssl
        )
        self._set_logging()

        if self.config.tor_client == 'deluge':
            from clients.deluge import DelugeClient
            if self.output:
                print('Generating Deluge Client')
            self.tor_client = DelugeClient(self.send_log,
                                           username=self.config.tor_client_user,
                                           password=self.config.tor_client_password,
                                           url=self.config.tor_client_url,
                                           hostname=self.config.hostname)

        elif self.config.tor_client == 'utorrent':
            from clients.utorrent import UTorrentClient
            if self.output:
                print('Generating uTorrent Client')
            self.tor_client = UTorrentClient(self.send_log,
                                             username=self.config.tor_client_user,
                                             password=self.config.tor_client_password,
                                             url=self.config.tor_client_url,
                                             hostname=self.config.hostname)

        elif self.config.tor_client == 'rtorrent':
            from clients.rtorrent import rTorrentClient
            if self.output:
                print('Generating rTorrent Client')
            self.tor_client = rTorrentClient(self.send_log,
                                             username=None,
                                             password=None,
                                             url=self.config.tor_client_url,
                                             hostname=self.config.hostname)

    def _set_logging(self):
        """
        Create the logger object if enabled in the config
        :return: None
        """

        if self.config.logging:
            if self.output:
                print('Logging is enabled.  Log output will be sent to {}'.format(self.config.logging_file))
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(self.config.logging_level)
            formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
            fhandle = logging.FileHandler(self.config.logging_file)
            fhandle.setFormatter(formatter)
            self.logger.addHandler(fhandle)

    def send_log(self, msg, level):
        """
        Used as a shim to write log messages.  Allows us to sanitize input before logging
        :param msg: Message to log
        :param level: Level to log message at
        :return: None
        """

        if not self.logger:
            return

        if self.output and self.config.valid_log_levels[level.upper()] >= self.config.logging_print_threshold:
            print(msg)

        # Make sure a good level was given
        if not hasattr(self.logger, level):
            self.logger.error('Invalid log level provided to send_log')
            return

        output = self._sanitize_log_message(msg)

        log_method = getattr(self.logger, level)
        log_method(output)

    def _sanitize_log_message(self, msg):
        """
        Take the incoming log message and clean and sensitive data out
        :param msg: incoming message string
        :return: cleaned message string
        """

        if not self.config.logging_censor:
            return msg

        # Remove server addresses
        msg = msg.replace(self.config.tor_client_url, 'http://*******:8112/json')

        # Remove IP addresses
        for match in re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", msg):
            msg = msg.replace(match, '***.***.***.***')

        return msg

    def write_influx_data(self, json_data):
        """
        Writes the provided JSON to the database
        :param json_data:
        :return:
        """
        self.send_log(json_data, 'info')

        # TODO This bit of fuckery may turn out to not be a good idea.
        """
        The idea is we only write 1 series at a time but we can get passed a bunch of series.  If the incoming list
        has more than 1 thing, we know it's a bunch of points to write.  We loop through them and recursively call this
        method which each series.  The recursive call will only have 1 series so it passes on to be written to Influx.

        I know, brilliant right?  Probably not
        """
        if len(json_data) > 1:
            for series in json_data:
                self.write_influx_data(series)
            return

        try:
            self.influx_client.write_points(json_data)
        except (InfluxDBClientError, ConnectionError, InfluxDBServerError) as e:
            if hasattr(e, 'code') and e.code == 404:

                msg = 'Database {} Does Not Exist.  Attempting To Create'.format(self.config.influx_database)
                self.send_log(msg, 'error')

                # TODO Grab exception here
                self.influx_client.create_database(self.config.influx_database)
                self.influx_client.write_points(json_data)

                return

            self.send_log('Failed to write data to InfluxDB', 'error')

            print('ERROR: Failed To Write To InfluxDB')
            print(e)

        self.send_log('Written To Influx: {}'.format(json_data), 'debug')



    def run(self):
        while True:
            self.tor_client.get_all_torrents()
            torrent_json = self.tor_client.process_torrents()
            if torrent_json:
                self.write_influx_data(torrent_json)
            #self.tor_client.get_active_plugins()
            tracker_json = self.tor_client.process_tracker_list()
            if tracker_json:
                self.write_influx_data(tracker_json)
            time.sleep(self.delay)




def main():

    parser = argparse.ArgumentParser(description="A tool to send Torrent Client statistics to InfluxDB")
    parser.add_argument('--config', default='config.ini', dest='config', help='Specify a custom location for the config file')
    # Silent flag allows output prior to the config being loaded to also be suppressed
    parser.add_argument('--silent', action='store_true', help='Surpress All Output, regardless of config settings')
    args = parser.parse_args()
    monitor = influxdbSeedbox(silent=args.silent, config=args.config)
    monitor.run()


if __name__ == '__main__':
    main()
