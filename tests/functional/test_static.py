from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from __future__ import unicode_literals
#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

from allura.tests import TestController


class TestStatic(TestController):

    def test_static_controller(self):
        self.app.get('/nf/_static_/wiki/js/browse.js')
        self.app.get('/nf/_static_/wiki/js/no_such_file.js', status=404)
        self.app.get('/nf/_static_/no_such_tool/js/comments.js', status=404)