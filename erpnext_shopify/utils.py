from __future__ import division
import frappe, math
from frappe.utils import get_request_session
from frappe.exceptions import AuthenticationError, ValidationError
from functools import wraps

import hashlib, base64, hmac, json

def get_collection_pages_number(type):
	return int(math.ceil(get_request('/admin/' + type + '/count.json').get('count') / 250))

def get_shopify_items():
	products = []
	for x in range(1, get_collection_pages_number('products') + 1):
		products.extend(get_request('/admin/products.json?limit=250&page=' + x)['products'])
	return products

def get_shopify_orders():
	orders = []
	for x in range(1, get_collection_pages_number('orders') + 1):
		orders.extend(get_request('/admin/orders.json?limit=250&page=' + x)['orders'])
	return orders

def get_country():
	countries = []
	for x in range(1, get_collection_pages_number('countries') + 1):
		countries.extend(get_request('/admin/countries.json?limit=250&page=' + x)['countries'])
	return countries
	
def get_shopify_customers():
	customers = []
	for x in range(1, get_collection_pages_number('customers') + 1):
		customers.extend(get_request('/admin/customers.json?limit=250&page=' + x)['customers'])
	return customers

def get_address_type(i):
	return ["Billing", "Shipping", "Office", "Personal", "Plant", "Postal", "Shop", "Subsidiary", "Warehouse", "Other"][i]

def create_webhook(topic, address):
	post_request('admin/webhooks.json', json.dumps({
		"webhook": {
			"topic": topic,
			"address": address,
			"format": "json"
		}
	}))

def shopify_webhook(f):
	"""
	A decorator thats checks and validates a Shopify Webhook request.
	"""
 
	def _hmac_is_valid(body, secret, hmac_to_verify):
		secret = str(secret)
		hash = hmac.new(secret, body, hashlib.sha256)
		hmac_calculated = base64.b64encode(hash.digest())
		return hmac_calculated == hmac_to_verify
 
	@wraps(f)
	def wrapper(*args, **kwargs):
		# Try to get required headers and decode the body of the request.
		try:
			webhook_topic = frappe.local.request.headers.get('X-Shopify-Topic')
			webhook_hmac	= frappe.local.request.headers.get('X-Shopify-Hmac-Sha256')
			webhook_data	= frappe._dict(json.loads(frappe.local.request.get_data()))
		except:
			raise ValidationError()

		# Verify the HMAC.
		if not _hmac_is_valid(frappe.local.request.get_data(), get_shopify_settings().password, webhook_hmac):
			raise AuthenticationError()

			# Otherwise, set properties on the request object and return.
		frappe.local.request.webhook_topic = webhook_topic
		frappe.local.request.webhook_data  = webhook_data
		kwargs.pop('cmd')
		
		return f(*args, **kwargs)
	return wrapper
	
@frappe.whitelist(allow_guest=True)
@shopify_webhook
def webhook_handler():
	from webhooks import handler_map
	topic = frappe.local.request.webhook_topic
	data = frappe.local.request.webhook_data
	handler = handler_map.get(topic)
	if handler:
		handler(data)

def get_shopify_settings():
	d = frappe.get_doc("Shopify Settings")
	return d.as_dict()
	
def get_request(path):
	s = get_request_session()
	url = get_shopify_url(path)
	r = s.get(url, headers=get_header())
	r.raise_for_status()
	return r.json()
	
def post_request(path, data):
	s = get_request_session()
	url = get_shopify_url(path)
	r = s.post(url, data=json.dumps(data), headers=get_header())
	r.raise_for_status()
	return r.json()

def delete_request(path):
	s = get_request_session()
	url = get_shopify_url(path)
	r = s.delete(url)
	r.raise_for_status()

def get_shopify_url(path):
	settings = get_shopify_settings()
	if settings['app_type'] == "Private":
		return 'https://{}:{}@{}/{}'.format(settings['api_key'], settings['password'], settings['shopify_url'], path)
	else:
		return 'https://{}/{}'.format(settings['shopify_url'], path)
		
def get_header():
	header = {'Content-type': 'application/json'}
	settings = get_shopify_settings()
	
	if settings['app_type'] == "Private":
		return header
	else:
		header["X-Shopify-Access-Token"] = settings['access_token']
		return header
	
def delete_webhooks():
	webhooks = get_webhooks()
	for webhook in webhooks:
		delete_request("/admin/webhooks/{}.json".format(webhook['id']))

def get_webhooks():
	webhooks = get_request("/admin/webhooks.json")
	return webhooks["webhooks"]
	
def create_webhooks():
	settings = get_shopify_settings()
	for event in ["orders/create", "orders/delete", "orders/updated", "orders/paid", "orders/cancelled", "orders/fulfilled", 
					"orders/partially_fulfilled", "order_transactions/create", "carts/create", "carts/update", 
					"checkouts/create", "checkouts/update", "checkouts/delete", "refunds/create", "products/create", 
					"products/update", "products/delete", "collections/create", "collections/update", "collections/delete", 
					"customer_groups/create", "customer_groups/update", "customer_groups/delete", "customers/create", 
					"customers/enable", "customers/disable", "customers/update", "customers/delete", "fulfillments/create", 
					"fulfillments/update", "shop/update", "disputes/create", "disputes/update", "app/uninstalled", 
					"channels/delete", "product_publications/create", "product_publications/update", 
					"product_publications/delete", "collection_publications/create", "collection_publications/update", 
					"collection_publications/delete", "variants/in_stock", "variants/out_of_stock"]:
					
		create_webhook(event, settings.webhook_address)