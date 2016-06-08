from __future__ import division, unicode_literals
import frappe, math, json
from frappe.utils import get_request_session
from frappe.exceptions import AuthenticationError, ValidationError
from .exceptions import ShopifySetupError
from functools import wraps

import hashlib, base64, hmac, json

def get_collection_pages_number(type):
	return int(math.ceil(get_request('/admin/' + type + '/count.json').get('count') / 250))

def get_shopify_items():
	products = []
	for x in range(1, get_collection_pages_number('products') + 1):
		products.extend(get_request('/admin/products.json?limit=250&page=' + str(x))['products'])
	return products

def get_shopify_orders():
	orders = []
	for x in range(1, get_collection_pages_number('orders') + 1):
		orders.extend(get_request('/admin/orders.json?limit=250&page=' + str(x))['orders'])
	return orders

def get_country():
	countries = []
	for x in range(1, get_collection_pages_number('countries') + 1):
		countries.extend(get_request('/admin/countries.json?limit=250&page=' + str(x))['countries'])
	return countries
	
def get_shopify_customers():
	customers = []
	for x in range(1, get_collection_pages_number('customers') + 1):
		customers.extend(get_request('/admin/customers.json?limit=250&page=' + str(x))['customers'])
	return customers

# Just in case later using
def get_shopify_customer_by_id(customerId):
	customer = None
	try:
		customer = get_request('/admin/customers/' + str(customerId) + '.json')['customer']
	except Exception, e:
		pass
	else:
		pass
	finally:
		pass

	return customer

def get_collection_by_product_id(product_id):
	collections = None
	try:
		collections = get_request('/admin/custom_collections.json?product_id=' + str(product_id))['custom_collections']
	except Exception, e:
		pass
	else:
		pass
	finally:
		pass
		
	return collections


def disable_shopify_sync_for_item(item, rollback=False):
	"""Disable Item if not exist on shopify"""
	if rollback:
		frappe.db.rollback()
		
	item.sync_with_shopify = 0
	item.sync_qty_with_shopify = 0
	item.save(ignore_permissions=True)
	frappe.db.commit()

def disable_shopify_sync_on_exception():
	frappe.db.rollback()
	frappe.db.set_value("Shopify Settings", None, "enable_shopify", 0)
	frappe.db.commit()

def is_shopify_enabled():
	shopify_settings = frappe.get_doc("Shopify Settings")
	if not shopify_settings.enable_shopify:
		return False
	try:
		shopify_settings.validate()
	except ShopifySetupError:
		return False
	
	return True
	
def make_shopify_log(title="Sync Log", status="Queued", method="sync_shopify", message=None, exception=False, 
name=None, request_data={}):
	if not name:
		name = frappe.db.get_value("Shopify Log", {"status": "Queued"})
		
		if name:
			""" if name not provided by log calling method then fetch existing queued state log"""
			log = frappe.get_doc("Shopify Log", name)
		
		else:
			""" if queued job is not found create a new one."""
			log = frappe.get_doc({"doctype":"Shopify Log"}).insert(ignore_permissions=True)
		
		if exception:
			frappe.db.rollback()
			log = frappe.get_doc({"doctype":"Shopify Log"}).insert(ignore_permissions=True)
			
		log.message = message if message else frappe.get_traceback()
		log.title = title[0:140]
		log.method = method
		log.status = status
		log.request_data= json.dumps(request_data)
		
		log.save(ignore_permissions=True)
		frappe.db.commit()