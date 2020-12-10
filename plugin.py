import os
import logging
import re

from gi.repository import Pango
from gi.repository import Gtk
from gi.repository import Gdk

import gajim
from gajim.plugins import GajimPlugin
from gajim.common import app
from gajim.gtk.util import get_cursor

log = logging.getLogger('gajim.p.point')

class PointimPlugin(GajimPlugin):

    def init(self):
        self.change_cursor = False
        self.gui_extension_points = {
            'chat_control_base': (self._on_connect_chat_control_base, self._on_disconnect_chat_control_base),
            # 'print_real_text': (self._on_print_real_text, None),
            'print_special_text': (self._on_print_special_text, None)
        }
        self.chat_control = None
        self.config = {
            # 'LINK_COLOR': ('#B8833E', 'Point.im link color')
            'LINK_COLOR': '#B8833E'
        }

    def _on_connect_chat_control_base(self, chat_control):
        if chat_control.contact.jid != 'p@point.im':
            return
        self.chat_control = chat_control
        self.textview = self.chat_control.conv_textview
        buf = self.textview.tv.get_buffer()
        self.textview.tagMessageId = buf.create_tag('pointim_message_id')
        self.textview.tagMessageId.set_property('foreground', self.config['LINK_COLOR'])
        self.textview.tagMessageId.set_property('underline', Pango.Underline.SINGLE)
        hid = self.textview.tagMessageId.connect('event', self.message_id_hyperlink_handler, 'pointim_message_id')
        self.chat_control.handlers[hid] = self.textview.tagMessageId

        hid = self.textview.tv.connect('motion_notify_event', self._on_motion_notify_event)
        self.chat_control.handlers[hid] = self.textview.tv

        # self.chat_control = chat_control
        # hid = textview.connect('text-changed', self.)
        self.update_special_text_match_patterns()

    def _on_disconnect_chat_control_base(self, chat_control):
        # log.error('Disconnected: {0}'.format(chat_control.contact.jid))
        pass

    # def _on_print_real_text(self, textview, text, other_tags, graphics, iterator, additional):
    #     log.error('Real text: {0}'.format(text))
    #     #textview.plugin_modified = True

    def _on_print_special_text(self, _textview, text, other_tags, graphics, additional_data, iterator):
        # log.error('Special text: {0}'.format(text))
        if self.message_id_pattern_re.match(text):
            buf, iter_, tag = self.get_iter_and_tag('pointim_message_id')
            buf.insert_with_tags(iterator, text, tag)
            self.textview.plugin_modified = True
            return

    def message_id_hyperlink_handler(self, texttag, widget, event, iter_, kind):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            begin_iter = iter_.copy()
            # we get the beginning of the tag
            while not begin_iter.starts_tag(texttag):
                begin_iter.backward_char()
            end_iter = iter_.copy()
            # we get the end of the tag
            while not end_iter.ends_tag(texttag):
                end_iter.forward_char()

            buf = self.textview.tv.get_buffer()
            word = buf.get_text(begin_iter, end_iter, True)

            if event.button.button == 1:
                # left click
                if kind == 'pointim_message_id':
                    self.insert_input(widget, word)
            elif event.button.button == 3:
                log.debug('right click')
                # TODO: show our context menu
                # return Gdk.EVENT_STOP

    def update_special_text_match_patterns(self):
        message_id_pattern = r'#[a-z]+(\/\d+)?'
        self.message_id_pattern_re = re.compile(message_id_pattern)
        pointim_patterns = '|' + message_id_pattern
        app.interface.basic_pattern = app.interface.basic_pattern + pointim_patterns
        app.interface._basic_pattern_re = re.compile(app.interface.basic_pattern, re.IGNORECASE)
        app.interface.emot_and_basic = app.interface.emot_and_basic + pointim_patterns
        app.interface._emot_and_basic_re = re.compile(app.interface.emot_and_basic, re.IGNORECASE)

    def get_iter_and_tag(self, tag_name):
        buffer_ = self.textview.tv.get_buffer()
        ttable = buffer_.get_tag_table()
        tag = ttable.lookup(tag_name)
        return buffer_, buffer_.get_end_iter(), tag

    def _on_motion_notify_event(self, widget, event):
        window = widget.get_window(Gtk.TextWindowType.TEXT)
        x, y = self.textview.tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        iter_ = self.textview.tv.get_iter_at_location(x, y)
        if isinstance(iter_, tuple):
            iter_ = iter_[1]
        tags = iter_.get_tags()
        tag_table = self.textview.tv.get_buffer().get_tag_table()
        if self.change_cursor:
            self.textview.tv.get_window(Gtk.TextWindowType.TEXT).set_cursor(get_cursor('default'))
            self.change_cursor = False
        for tag in tags:
            if tag in (self.textview.tagMessageId, ):
                self.textview.tv.get_window(Gtk.TextWindowType.TEXT).set_cursor(get_cursor('pointer'))
            self.change_cursor = True


    def insert_input(self, widget, text):
        text = text.strip() + ' '
        message_buffer = self.chat_control.msg_textview.get_buffer()
        message_buffer.insert_at_cursor(text)
        self.chat_control.msg_textview.grab_focus()
