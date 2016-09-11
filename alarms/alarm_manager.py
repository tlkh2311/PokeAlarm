# coding: utf8
#Setup Logging
import logging
log = logging.getLogger(__name__)

#Python Utility imports
import os
import json
import time
import base64
import traceback
import threading
import Queue
from datetime import datetime
from threading import Thread

#Local imports
from . import config
from utils import *

class Alarm_Manager(Thread):

	def __init__(self):
		#Intialize as Thread
		super(Alarm_Manager, self).__init__()
		#Import settings from Alarms.json
		with open(get_path(config['CONFIG_FILE'])) as file:
			settings = json.load(file)
			alarm_settings = settings["alarms"]
			config["NOTIFY_LIST"] = make_notify_list(settings["pokemon"])
			out = ""
			output_list = notify_list_lines(config["NOTIFY_LIST"],4)
			if len(output_list) == 0:
				log.info("No pokemon are set for notification.")
			else:
				log.info("You will be notified of the following pokemon:")
				for line in output_list:
					log.info(line)
			output_list_twitter = notify_list_multi_msgs(config["NOTIFY_LIST"],140)
			self.stop_list =  make_pokestops_list(settings["pokestops"])
			self.gym_list = make_gym_list(settings["gyms"])
			self.pokemon, self.pokestops, self.gyms   = {}, {}, {}
			self.alarms = []
			self.queue = Queue.Queue()
			self.data = {}
			self.lock = threading.Lock()
			for alarm in alarm_settings:
				if alarm['active'] == "True" :
					if alarm['type'] == 'boxcar' :
						from Boxcar import Boxcar_Alarm
						self.alarms.append(Boxcar_Alarm(alarm))
					elif alarm['type'] == 'pushbullet' :
						from Pushbullet import Pushbullet_Alarm
						self.alarms.append(Pushbullet_Alarm(alarm))
					elif alarm['type'] == 'pushover' :
						from Pushover import Pushover_Alarm
						self.alarms.append(Pushover_Alarm(alarm))
					elif alarm['type'] == 'slack' :
						from Slack import Slack_Alarm
						self.alarms.append(Slack_Alarm(alarm))
					elif alarm['type'] == 'telegram' :
						from Telegram import Telegram_Alarm
						self.alarms.append(Telegram_Alarm(alarm))
					elif alarm['type'] == 'twilio' :
						from Twilio import Twilio_Alarm
						self.alarms.append(Twilio_Alarm(alarm))
					elif alarm['type'] == 'twitter' :
						from Twitter import Twitter_Alarm
						self.alarms.append(Twitter_Alarm(alarm))
					else:
						log.info("Alarm type not found: " + alarm['type'])
					set_optional_args(str(alarm))
				else:
					log.info("Alarm not activated: " + alarm['type'] + " because value not set to \"True\"")
	
	#Update data about this request
	def update(self, id, info):
		self.lock.acquire()
		try:
			if id not in self.data:
				self.queue.put(id)
			self.data[id] = info #update info if changed
		finally:
			self.lock.release()
	
	#Threaded loop to process request data from the queue 
	def run(self):
		log.info("PokeAlarm has started! Your alarms should trigger now.")
		while True:
			try:
				count = 0;
				for i in range(5000): #Take a break and clean house every 5000 requests handled
					id = self.queue.get(block=True)
					self.lock.acquire()
					try: #Get id and remove data from the queue
						data = self.data[id]
						del self.data[id]
						self.queue.task_done()
					finally:
						self.lock.release()
					if 'may_extend' not in data['message']:
						data['message']['may_extend'] = False
					if data['type'] == 'pokemon' :
						log.debug("Request processing for Pokemon #%s" % data['message']['pokemon_id'])
						self.trigger_pokemon(data['message'])
						log.debug("Finished processing for Pokemon #%s" % data['message']['pokemon_id'])
					elif data['type'] == 'pokestop' : 
						log.debug("Request processing for Pokestop %s" % data['message']['pokestop_id'])
						self.trigger_pokestop(data['message'])
						log.debug("Finished processing for Pokestop %s" % data['message']['pokestop_id'])
					elif data['type'] == 'gym' or data['type'] == 'gym_details'  :
						log.debug("Request processing for Gym %s" % data['message'].get('gym_id', data['message'].get('id')))
						self.trigger_gym(data['message'])
						log.debug("Finished processing for Gym %s" % data['message'].get('gym_id', data['message'].get('id')))
					else:
						log.debug("Invalid type specified: %s" % data['type'])
				log.debug("Cleaning up 'seen' sets...")
				self.clear_stale();
			except Exception as e:
				log.error("Error while processing request: %s" % e)
				log.debug("Stack trace: \n {}".format(traceback.format_exc()))
				if data:
					log.debug("Request format: \n %s " % json.dumps(data, indent=4, sort_keys=True))
	#Send a notification to alarms about a found pokemon
	def trigger_pokemon(self, pkmn):
		#If already alerted, skip
		if "{}{}".format(pkmn['encounter_id'], pkmn['may_extend']) in self.pokemon:
			return
			
		#Mark the pokemon as seen along with exipre time
		dissapear_time = datetime.utcfromtimestamp(pkmn['disappear_time']);
		self.pokemon["{}{}".format(pkmn['encounter_id'], pkmn['may_extend'])] = dissapear_time
		pkmn_id = pkmn['pokemon_id']
		name = get_pkmn_name(pkmn_id)
		
		#Check if Pokemon is on the notify list
		if pkmn_id not in config["NOTIFY_LIST"]:
			log.info(name + " ignored: notify not enabled.")
			return
		
		#Check if the Pokemon has already expired
		seconds_left = (dissapear_time - datetime.utcnow()).total_seconds()
		if seconds_left < config['TIME_LIMIT'] :
			log.info(name + " ignored: not enough time remaining.")
			log.debug("Time left must be %f, but was %f." % (config['TIME_LIMIT'], seconds_left))
			return

		#Check if the Pokemon is outside of notify range
		lat = pkmn['latitude']
		lng = pkmn['longitude']
		dist = get_dist([lat, lng])
		if dist >= config["NOTIFY_LIST"][pkmn_id]:
			log.info(name + " ignored: outside range")
			log.debug("Pokemon must be less than %d, but was %d." % (config["NOTIFY_LIST"][pkmn_id], dist))
			return
        
		#Check if the Pokemon is in the geofence
		if 'GEOFENCE' in config:
			if config['GEOFENCE'].contains(lat,lng) is not True:
				log.info(name + " ignored: outside geofence")
				return
		
		if pkmn['may_extend']:
			may_extend = u" #會加時"
		else:
			may_extend = ""
				
		#Trigger the notifcations
		log.info(name + " notication was triggered!")
		timestamps = get_timestamps(dissapear_time)
		pkmn_info = {
			'id': str(pkmn_id),
 			'pkmn': name,
			'lat' : "{}".format(repr(lat)),
			'lng' : "{}".format(repr(lng)),
			'gmaps': get_gmaps_link(lat, lng),
			'time_left': timestamps[0] + may_extend,
			'dist': "%d%s" % (dist, 'yd' if config['UNITS'] == 'imperial' else 'm'),
			'12h_time': timestamps[1],
			'24h_time': timestamps[2],
			'dir': get_dir(lat,lng),
			'encounter_id': format(int(base64.b64decode( str( pkmn['encounter_id'] ) )), 'x')
		}
		pkmn_info = self.optional_arguments(pkmn_info)

                try:
                    pkmn_info['iv_a'] = str(pkmn.get('attack',  pkmn['pokemon_data'].get('individual_attack', 0)))
                    if pkmn_info['iv_a']  == 'None': pkmn_info['iv_a'] = "0"
                except:
                    pkmn_info['iv_a'] = "0"
                try:
                    pkmn_info['iv_s'] = str(pkmn.get('stamina', pkmn['pokemon_data'].get('individual_stamina', 0)))
                    if pkmn_info['iv_s']  == 'None': pkmn_info['iv_s'] = "0"
                except:
                    pkmn_info['iv_s'] = "0"
                try:
                    pkmn_info['iv_d'] = str(pkmn.get('defense', pkmn['pokemon_data'].get('individual_defense', 0)))
                    if pkmn_info['iv_d']  == 'None': pkmn_info['iv_d'] = "0"
                except:
                    pkmn_info['iv_d'] = "0"
                try:
                    pkmn_info['iv'] = "%0.2f" % ((float(pkmn_info['iv_a'])+float(pkmn_info['iv_s'])+float(pkmn_info['iv_d']))/45.0 * 100)
                except:
                    pkmn_info['iv'] = "??"
                    pass
                try:
                       pkmn_info['move_1'] = get_move_name(pkmn.get('move_1', pkmn['pokemon_data'].get('move_1', 0)))
                except:
                        pass
                try:
                       pkmn_info['move_2'] = get_move_name(pkmn.get('move_2', pkmn['pokemon_data'].get('move_2', 0)))
                except:
                        pass
                        

                if pkmn_info.get('move_1', None) is None or pkmn_info.get('move_1', '') == '':
                    pkmn_info['move_1'] = '??'
                    pkmn_info['move_2'] = '??'
                    pkmn_info['iv_a'] = '??'
                    pkmn_info['iv_d'] = '??'
                    pkmn_info['iv_s'] = '??'
                    pkmn_info['iv'] = '??'

		
			
		for alarm in self.alarms:
			alarm.pokemon_alert(pkmn_info)

	#Send a notication about Pokestop
	def trigger_pokestop(self, stop):
		#Check if stop is lured or not
		if stop['lure_expiration'] is None:
			return
		
		#If Lures are not enabled
		if self.stop_list.get('lured') is None:
			return
			
		#If already alerted, skip
		id = stop['pokestop_id']
		dissapear_time = datetime.utcfromtimestamp(stop['lure_expiration'])
		if id in self.pokestops and self.pokestops[id] == dissapear_time:
			return
		self.pokestops[id] = dissapear_time
		
		#Check if the Pokestop has already expired
		seconds_left = (dissapear_time - datetime.utcnow()).total_seconds()
		if seconds_left < config['TIME_LIMIT'] :
			log.info("Pokestop ignored: not enough time remaining.")
			log.debug("Time left must be %f, but was %f." % (config['TIME_LIMIT'], seconds_left))
			return
		
		#Check if the Pokestop is outside of notify range
		lat = stop['latitude']
		lng = stop['longitude']
		dist = get_dist([lat, lng])
		if dist >=  self.stop_list['lured']:
			log.info("Pokestop ignored: outside range")
			log.debug("Pokestop must be less than %d, but was %d." % (self.stop_list['lured'], dist))
			return
		
		#Check if the Pokestop is in the geofence
		if 'GEOFENCE' in config:
			if config['GEOFENCE'].contains(lat,lng) is not True:
				log.info("Pokestop ignored: outside geofence")
				return
		
		#Trigger the notifcations
		log.info("Pokestop notication was triggered!")
		timestamps = get_timestamps(dissapear_time)
		stop_info = {
			'id': id,
			'lat' : "{}".format(repr(lat)),
			'lng' : "{}".format(repr(lng)),
			'gmaps': get_gmaps_link(lat, lng),
			'dist': "%d%s" % (dist, 'yd' if config['UNITS'] == 'imperial' else 'm'),
			'time_left': timestamps[0],
			'12h_time': timestamps[1],
			'24h_time': timestamps[2],
			'dir': get_dir(lat,lng)
		}

		stop_info = self.optional_arguments(stop_info)
		
		for alarm in self.alarms:
			alarm.pokestop_alert(stop_info)

	
	#Send a notifcation about pokemon gym detected
	def trigger_gym(self, gym):
		id = gym.get('gym_id', gym.get('id'))
		old_team = self.gyms.get(id)
		new_team = gym.get('team_id', gym.get('team')) 	
		self.gyms[id] = new_team
		log.debug("Gym %s - %s to %s" % (id, old_team, new_team))
		
		#Check to see if the gym has changed 
		if old_team == None or new_team == old_team:
			log.debug("Gym ignored: no change detected")
			return #ignore neutral for now
		
		#Check for Alert settings
		old_team = get_team_name(old_team)
		new_team = get_team_name(new_team)
		max_dist = max(self.gym_list.get("From_%s" % old_team, -1), self.gym_list.get("To_%s" % new_team, -1))
		if max_dist is -1:
			log.info("Gym ignored: alert not set")
			return
			
		#Check if the Gym is outside of notify range
		lat = gym['latitude']
		lng = gym['longitude']
		dist = get_dist([lat, lng])
		if dist >= max_dist:
			log.info("Gym ignored: outside range")
			log.debug("Gym must be less than %d, but was %d." % (max_dist, dist))
			return
		
		#Check if the Gym is in the geofence
		if 'GEOFENCE' in config:
			if config['GEOFENCE'].contains(lat,lng) is not True:
				log.info("Gym ignored: outside geofence")
				return
		
		#Trigger the notifcations
		log.info("Gym notication was triggered!")
		gym_info = {
			'id': id,
			'lat' : "{}".format(repr(lat)),
			'lng' : "{}".format(repr(lng)),
			'gmaps': get_gmaps_link(lat, lng),
			'dist': "%d%s" % (dist, 'yd' if config['UNITS'] == 'imperial' else 'm'),
			'dir': get_dir(lat,lng),
			'points': str(gym.get('gym_points')),
			'old_team': old_team,
			'new_team': new_team
		}
		gym_info = self.optional_arguments(gym_info)
		
		for alarm in self.alarms:
			alarm.gym_alert(gym_info)
		
	#clear expired pokemon so that the seen set is not too large
	def clear_stale(self):
		for dict in (self.pokemon, self.pokestops):
			old = []
			for id in dict:
				if dict[id] < datetime.utcnow() :
					old.append(id)
			for id in old:
				del dict[id]
	
	#clear expired pokemon so that the seen set is not too large
	def optional_arguments(self, info):
		if config['REV_LOC']:
			info.update(**reverse_location(info))
		if config['DM_WALK']:
			info.update(**get_walking_data(info))
		if config['DM_BIKE']:
			info.update(**get_biking_data(info))
		if config['DM_DRIVE']:
			info.update(**get_driving_data(info))
		
		return info
