#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2013-2014, Epic Games, Inc.
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible. If not, see <http://www.gnu.org/licenses/>.
#

DOCUMENTATION = '''
---
module: zabbix_webcheck
short_description: Zabbix web checks creates/updates/deletes
description:
   - Creates a web check according to the host and application, the application will also be created if it does not exist.
   - When the webcheck already exists and scenario steps are changed, the webcheck will be updated and the steps will be replaced.
   - Delete webcheck from Zabbix if the webcheck exists.
version_added: "1.9"
author: Damon Chencheng Kong, Harrison Gu
requirements:
    - zabbix-api python module
options:
    server_url:
        description:
            - Url of Zabbix server, with protocol (http or https).
              C(url) is an alias for C(server_url).
        required: true
        default: null
        aliases: [ "url" ]
    login_user:
        description:
            - Zabbix user name.
        required: true
        default: null
    login_password:
        description:
            - Zabbix user password.
        required: true
        default: null
    timeout:
        description:
            - The timeout of API request(seconds).
        default: 10
    web_check:
        description:
            - List of web check to be created/updated/deleted (see example).
            - If the webcheck has already been added, the web_check_name won't be updated.
            - Available values: web_check_name(required), application_name(required), host_name(required), agent, authentication, delay, http_password, http_user, macros, nextcheck, status and web_check_steps(required)
            - Available scenario step(steps) values are: name(required), number(required), url(required), posts, required, status_codes and timeout
            - Please review the web check documentation for more information on the supported web check properties: https://www.zabbix.com/documentation/2.0/manual/appendix/api/webcheck/definitions
        required: true
    state:
        description:
            - Create/update/delete a web check.
            - Possible values are: present, absent, default create web check.
        required: false
        default: "present"
'''

EXAMPLES = '''
- name: create or update(if exists) a web check
  local_action:
    module: zabbix_webcheck
    server_url: http://monitor.example.com
    login_user: username
    login_password: password
    state: present
    web_check:
      web_check_name: example Health Check
      application_name: example app
      host_name: example host name
      agent: zabbix web check
      steps:
        - name: WebSite Check1
          url: http://www.example1.com
          number: 1
          status_codes: 200
        - name: WebSite Check2
          url: http://www.example2.com
          number: 2
          status_codes: 200

- name: delete a web check
  local_action:
    module: zabbix_webcheck
    server_url: http://monitor.example.com
    login_user: username
    login_password: password
    state: absent
    web_check:
      web_check_name: "example Health Check"
'''

import json
import base64
import urllib2
import random
import time

try:
    from zabbix_api import ZabbixAPI, ZabbixAPISubClass
    from zabbix_api import ZabbixAPIException
    from zabbix_api import Already_Exists

    HAS_ZABBIX_API = True
except ImportError:
    HAS_ZABBIX_API = False


class ZabbixAPIExtends(ZabbixAPI):
    webcheck = None

    def __init__(self, server, timeout, **kwargs):
        ZabbixAPI.__init__(self, server, timeout=timeout)
        self.webcheck = ZabbixAPISubClass(self, dict({"prefix": "webcheck"}, **kwargs))


class WebCheck(object):
    def __init__(self, module, zbx):
        self._module = module
        self._zapi = zbx

        self._web_scenario_optional_properties_list = ['agent', 'authentication', 'delay', 'http_password',
                                                       'http_user', 'macros', 'status']
        self._scenario_step_optional_properties_list = ['posts', 'required', 'status_codes', 'timeout']

    # get host id by host name
    def get_host_id(self, host_name):
        try:
            host_list = self._zapi.host.get({'output': 'extend', 'filter': {'host': host_name}})
            if len(host_list) < 1:
                self._module.fail_json(msg="Host not found: %s" % host_name)
            else:
                host_id = host_list[0]['hostid']
                return host_id
        except Exception, e:
            self._module.fail_json(msg="Failed to get the host %s id: %s." % (host_name, e))

    # get application id by application name, host id
    def get_application_id(self, host_id, application_name):
        application_id = None
        try:
            application_list = self._zapi.application.get(
                {'output': 'extend', 'filter': {'hostid': host_id, "name": application_name}})
            if len(application_list) < 1:
                applicationids = self.create_application(host_id, application_name)
                if len(applicationids) > 0:
                    application_id = applicationids['applicationids'][0]
            else:
                application_id = application_list[0]['applicationid']
            return application_id
        except Exception, e:
            self._module.fail_json(msg="Failed to get the application '%s' id: %s." % (application_name, e))

    #get webcheck
    def get_web_check(self, web_check_name, host_id):
        try:
            web_check_list = self._zapi.webcheck.get(
                {"output": "extend", "selectSteps": "extend", 'hostids': [host_id], 'filter': {'name': web_check_name}})
            if len(web_check_list) > 0:
                return web_check_list[0]
            return None
        except Exception, e:
            self._module.fail_json(msg="Failed to get WebCheck %s: %s" % (web_check_name, e))

    # get steps
    def get_steps(self, web_check_steps):
        steps = []
        (name, url, no) = (None, None, None)
        if web_check_steps:
            for web_check_step in web_check_steps:
                if "name" in web_check_step and web_check_step["name"]:
                    name = web_check_step["name"]
                else:
                    self._module.fail_json(msg="The \"name\" property in scenario steps is required and not null.")

                if "url" in web_check_step and web_check_step["url"]:
                    url = web_check_step["url"]
                else:
                    self._module.fail_json(msg="The \"url\" property in scenario steps is required and not null.")

                if "number" in web_check_step and web_check_step["number"]:
                    no = web_check_step["number"]
                else:
                    self._module.fail_json(msg="The \"no\" property in scenario steps is required and not null.")

                optional_step_properties = dict()
                for step_optional_property in self._scenario_step_optional_properties_list:
                    value = self.get_value_by_key(web_check_step, step_optional_property)
                    if value is not None:
                        optional_step_properties[step_optional_property] = value

                step = {"name": name,
                        "url": url,
                        "no": no}
                steps.append(dict(step, **optional_step_properties))
            return steps
        else:
            self._module.fail_json(msg="The \"steps\" property in scenario is required and not null.")

    def get_replace_steps(self, old_webcheck_steps, new_webcheck_steps, httptestid):
        relace_webcheck_steps = []

        if new_webcheck_steps:
            for new_webcheck_step in new_webcheck_steps:
                flag = False
                for old_webcheck_step in old_webcheck_steps:
                    new_webcheck_step_url = new_webcheck_step['url']
                    old_webcheck_step_url = old_webcheck_step['url']
                    if new_webcheck_step_url == old_webcheck_step_url:
                        # update
                        new_webcheck_step['webstepid'] = old_webcheck_step['webstepid']
                        new_webcheck_step['httptestid'] = httptestid
                        relace_webcheck_steps.append(new_webcheck_step)
                        flag = True
                        break
                if not flag:
                    # add
                    new_webcheck_step['httptestid'] = httptestid
                    relace_webcheck_steps.append(new_webcheck_step)
        return relace_webcheck_steps

    # get webcheck params
    def get_web_check_params(self, web_check_name, host_id, application_id, web_check_steps, optional_scenario_values,
                             web_check_obj=None, httptestid=None):
        steps = self.get_steps(web_check_steps)
        if httptestid:
            if web_check_obj['steps']:
                if type(web_check_obj['steps']) is list:
                    old_steps = web_check_obj['steps']
                elif type(web_check_obj['steps']) is dict:
                    old_steps = list(web_check_obj['steps'].values())
                else:
                    old_steps = None
                steps = self.get_replace_steps(old_steps, steps, httptestid)
        web_check_params = {"name": web_check_name,
                            "applicationid": application_id,
                            "hostid": host_id,
                            "steps": steps}
        if httptestid:
            web_check_params['httptestid'] = httptestid
        return dict(web_check_params, **optional_scenario_values)

    #create application by application name, host id
    def create_application(self, hostId, applicationName):
        try:
            if self._module.check_mode:
                self._module.exit_json(changed=True)
            return self._zapi.application.create({'hostid': hostId, 'name': applicationName})
        except Exception, e:
            self._module.fail_json(msg="Failed to create Application '%s': %s" % (applicationName, e))

    # create web check
    def create_web_check(self, web_check_name, host_id, application_id, web_check_steps, scenario_optional_properties):
        web_check_params = self.get_web_check_params(web_check_name, host_id, application_id, web_check_steps,
                                                     scenario_optional_properties)
        try:
            if self._module.check_mode:
                self._module.exit_json(changed=True)
            self._zapi.webcheck.create(web_check_params)
            self._module.exit_json(changed=True, result="Successfully added WebCheck %s " % web_check_name)
        except Exception, e:
            self._module.fail_json(msg="Failed to create WebCheck %s: %s" % (web_check_name, e))

    # update web check
    def update_web_check(self, web_check_obj, web_check_name, host_id, application_id, web_check_steps,
                         scenario_optional_properties):
        httptestid = web_check_obj['httptestid']
        web_check_params = self.get_web_check_params(web_check_name, host_id, application_id, web_check_steps,
                                                     scenario_optional_properties, web_check_obj, httptestid)
        try:
            if self._module.check_mode:
                self._module.exit_json(changed=True)
            self._zapi.webcheck.update(web_check_params)
            self._module.exit_json(changed=True, result="Successfully updated WebCheck %s " % web_check_params)
        except Exception, e:
            self._module.fail_json(msg="Failed to updated WebCheck %s: %s" % (web_check_params, e))

    # delete web check
    def delete_web_check(self, webCheckObj):
        webCheckId = webCheckObj['httptestid']
        webCheckName = webCheckObj['name']
        try:
            if self._module.check_mode:
                self._module.exit_json(changed=True)
            self._zapi.webcheck.delete([webCheckId])
            self._module.exit_json(changed=True, result="Successfully deleted WebCheck %s " % webCheckName)
        except Exception, e:
            self._module.fail_json(msg="Failed to delete WebCheck %s: %s" % (webCheckName, e))

    def get_value_by_key(self, dict_obj, key):
        if key in dict_obj:
            value = dict_obj[key]
            return value
        return None


def main():
    module = AnsibleModule(
        argument_spec=dict(
            server_url=dict(required=True),
            login_user=dict(required=True),
            login_password=dict(required=True),
            state=dict(default="present"),
            timeout=dict(default=10),
            web_check=dict(required=True),
        ),
        supports_check_mode=True,
    )

    if not HAS_ZABBIX_API:
        module.fail_json(msg="Missing requried zabbix-api module (check docs or install with: pip install zabbix-api)")

    server_url = module.params['server_url']
    login_user = module.params['login_user']
    login_password = module.params['login_password']
    state = module.params['state']
    timeout = module.params['timeout']
    web_check = module.params['web_check']

    web_check_name = ''
    if 'web_check_name' in web_check:
        web_check_name = web_check['web_check_name']
    else:
        module.fail_json(msg="The \"web_check_name\" property is required and not null.")

    # login to zabbix
    zbx = None
    try:
        zbx = ZabbixAPIExtends(server_url, timeout=timeout)
        zbx.login(login_user, login_password)
    except Exception, e:
        module.fail_json(msg="Failed to connect to Zabbix server: %s" % e)

    # new a WebCheck class object
    web_check_class_obj = WebCheck(module, zbx)

    host_name = web_check_class_obj.get_value_by_key(web_check, "host_name")
    if not host_name:
        module.fail_json(msg="The \"host_name\" property is required.")
    host_id = web_check_class_obj.get_host_id(host_name)

    # get webcheck object by name
    web_check_obj = web_check_class_obj.get_web_check(web_check_name, host_id)

    if state == 'absent':
        if not web_check_obj:
            module.exit_json(changed=False, msg="WebCheck %s does not exist" % web_check_name)
        else:
            # delete a webcheck
            web_check_class_obj.delete_web_check(web_check_obj)
    else:
        application_name = web_check_class_obj.get_value_by_key(web_check, "application_name")
        if not application_name:
            module.fail_json(msg="The \"application_name\" property is required.")

        host_id = web_check_class_obj.get_host_id(host_name)
        application_id = web_check_class_obj.get_application_id(host_id, application_name)

        web_check_steps = web_check_class_obj.get_value_by_key(web_check, "steps")

        scenario_optional_properties = dict()
        for optional_property in web_check_class_obj._web_scenario_optional_properties_list:
            value = web_check_class_obj.get_value_by_key(web_check, optional_property)
            if value is not None:
                scenario_optional_properties[optional_property] = value

        if not web_check_obj:
            # create webcheck
            web_check_class_obj.create_web_check(web_check_name, host_id, application_id, web_check_steps,
                                                 scenario_optional_properties)
        else:
            # update webcheck
            web_check_class_obj.update_web_check(web_check_obj, web_check_name, host_id, application_id,
                                                 web_check_steps, scenario_optional_properties)

from ansible.module_utils.basic import *
main()

