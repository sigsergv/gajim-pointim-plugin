# -*- coding: utf-8 -*-

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import Pango

import re
import os
import time
import locale
import sqlite3
import json

from gajim.common import helpers
from gajim.common import app
from gajim.plugins import GajimPlugin
from gajim.plugins.helpers import log_calls, log
from gajim.plugins.gui import GajimPluginConfigDialog
from gajim.conversation_textview import TextViewImage
from gajim import gtkgui_helpers
from urllib.request import urlopen

#nb_xmpp = False
#import common.xmpp
#if not dir(common.xmpp):
#    import nbxmpp
#    nb_xmpp = True

class PointimPlugin(GajimPlugin):
    @log_calls('PointimPlugin')
    def init(self):
        self.description = _('Clickable Point.im links , Point.im nicks, '
            'preview Point.im pictures (not yet).\nThe key combination alt + up in the '
            'textbox allow insert the number of last message '
            '(comment or topic).')
        print(PointimPluginConfigDialog)
        self.config_dialog = PointimPluginConfigDialog(self)
        self.gui_extension_points = {
                'chat_control_base': (self.connect_with_chat_control,
                                       self.disconnect_from_chat_control),
                'print_special_text': (self.print_special_text,
                                       self.print_special_text1),}
        self.config_default_values = {'SHOW_AVATARS': (False, ''),
                    'AVATAR_SIZE': (20, 'Avatar size(10-32)'),
                    'avatars_old': (2419200, 'Update avatars '
                        'who are older 28 days'),
                    'SHOW_PREVIEW': (False, ''),
                    'PREVIEW_SIZE': (150, 'Preview size(10-512)'),
                    'LINK_COLOR': ('#B8833E', 'Point.im link color'),
                    'SHOW_TAG_BUTTON': (True, ''),
                    'ONLY_AUTHOR_AVATAR': (True, ''),
                    'ONLY_FIRST_AVATAR': (False, ''),
                    'MENUITEM1': ('tune', ''), 'MENUITEM_TEXT1': ('*tune', ''),
                    'MENUITEM2': ('geo', ''), 'MENUITEM_TEXT2': ('*geo', ''),
                    'MENUITEM3': ('gajim', ''),
                    'MENUITEM_TEXT3': ('*gajim', ''),
                    'MENUITEM4': ('me', ''), 'MENUITEM_TEXT4': ('/me', ''),
                    'MENUITEM5': ('', ''), 'MENUITEM_TEXT5': ('', ''),
                    'MENUITEM6': ('', ''), 'MENUITEM_TEXT6': ('', ''),
                    'MENUITEM7': ('', ''), 'MENUITEM_TEXT7': ('', ''),
                    'MENUITEM8': ('', ''), 'MENUITEM_TEXT8': ('', ''),
                    'MENUITEM9': ('', ''), 'MENUITEM_TEXT9': ('', ''),
                    'MENUITEM10': ('', ''), 'MENUITEM_TEXT10': ('', ''), }
        self.chat_control = None
        self.controls = []
        self.conn = None
        self.cache_path = os.path.join(app.AVATAR_PATH, 'pointim')
        if not os.path.isdir(self.cache_path):
            os.makedirs(self.cache_path)

    @log_calls('PointimPlugin')
    def connect_with_chat_control(self, chat_control):
        if chat_control.contact.jid != 'p@point.im':
            return

        self.chat_control = chat_control
        control = Base(self, self.chat_control)
        self.controls.append(control)
        self.conn = sqlite3.connect(os.path.join(self.cache_path, 'pointim_db'))
        self.conn.execute('create table if not exists person'
            '(nick, id, last_modified)')
        self.cursor = self.conn.cursor()

    @log_calls('PointimPlugin')
    def disconnect_from_chat_control(self, chat_control):
        for control in self.controls:
            control.disconnect_from_chat_control()
        self.controls = []
        if self.conn:
            self.conn.close()

    def print_special_text(self, tv, special_text, other_tags, graphics=True,
        iter_=None, additional_data=None):
        for control in self.controls:
            if control.chat_control.conv_textview != tv:
                continue
            control.print_special_text(special_text, other_tags, graphics=True)

    def print_special_text1(self, chat_control, special_text, other_tags=None,
        graphics=True, iter_=None, additional_data=None):
        for control in self.controls:
            if control.chat_control == chat_control:
                control.disconnect_from_chat_control()
                self.controls.remove(control)

class Base(object):
    def __init__(self, plugin, chat_control):
        self.last_pointim_num = ''
        self.plugin = plugin
        self.chat_control = chat_control
        self.textview = self.chat_control.conv_textview
        self.change_cursor = False

        id_ = self.textview.tv.connect('button_press_event',
            self.on_textview_button_press_event, self.textview)
        chat_control.handlers[id_] = self.textview.tv

        id_ = self.chat_control.msg_textview.connect('key_press_event',
            self.mykeypress_event)
        chat_control.handlers[id_] = self.chat_control.msg_textview

        self.id_ = self.textview.tv.connect('motion_notify_event',
            self.on_textview_motion_notify_event)
        self.chat_control.handlers[self.id_] = self.textview.tv

        # new buffer tags
        color = self.plugin.config['LINK_COLOR']
        buffer_ = self.textview.tv.get_buffer()
        self.textview.tagSharpSlash = buffer_.create_tag('pointim_sharp_slash')
        self.textview.tagSharpSlash.set_property('foreground', color)
        self.textview.tagSharpSlash.set_property('underline',
            Pango.Underline.SINGLE)
        id_ = self.textview.tagSharpSlash.connect('event',
            self.pointim_hyperlink_handler, 'pointim_sharp_slash')
        chat_control.handlers[id_] = self.textview.tagSharpSlash

        self.textview.tagPointimNick = buffer_.create_tag('pointim_nick')
        self.textview.tagPointimNick.set_property('foreground', color)
        self.textview.tagPointimNick.set_property('underline',
            Pango.Underline.SINGLE)
        id_ = self.textview.tagPointimNick.connect('event',
            self.pointim_hyperlink_handler, 'pointim_nick')
        chat_control.handlers[id_] = self.textview.tagPointimNick
        self.textview.tagPointimPic = buffer_.create_tag('pointim_pic')

        self.create_patterns()
        self.create_link_menu()
        self.create_tag_menu()
        try:
            self.create_buttons()
        except Exception:
            pass

    def create_patterns(self):
        self.pointim_post_uid = self.pointim_nick = ''
        self.pointim_post_re = re.compile(r'#([a-z]+)')
        self.pointim_post_comment_re = re.compile(r'#([a-z]+)/(\d+)')
        pointim_sharp_slash = r'#[a-z]+(\/\d+)?'
        pointim_nick = r'@[a-zA-Z0-9_@:\.-]+'
        pointim_pic = r'http://i\.pointim\.com/.+/[0-9-]+\.[JPG|jpg]'
        interface = app.interface
        interface.pointim_sharp_slash_re = re.compile(pointim_sharp_slash)
        self.pointim_nick_re = interface.pointim_nick_re = re.compile(pointim_nick)
        self.pointim_pic_re = interface.pointim_pic_re = re.compile(pointim_pic)
        pointim_pattern = '|' + pointim_sharp_slash + '|' + pointim_nick + '|' + pointim_pic
        interface.basic_pattern = interface.basic_pattern + pointim_pattern
        interface.emot_and_basic = interface.emot_and_basic + pointim_pattern
        interface._basic_pattern_re = re.compile(interface.basic_pattern,
            re.IGNORECASE)
        interface._emot_and_basic_re = re.compile(interface.emot_and_basic,
            re.IGNORECASE + re.UNICODE)

    def create_buttons(self):
        # create pointim button
        actions_hbox = self.chat_control.xml.get_object('actions_hbox')
        self.button = Gtk.Button(label=None, stock=None, use_underline=True)
        self.button.set_property('relief', Gtk.ReliefStyle.NONE)
        self.button.set_property('can-focus', False)
        img = Gtk.Image()
        img_path = self.plugin.local_file_path('pointim.png')
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(img_path)
        iconset = Gtk.IconSet(pixbuf=pixbuf)
        factory = Gtk.IconFactory()
        factory.add('pointim', iconset)
        factory.add_default()
        img.set_from_icon_name('pointim', Gtk.IconSize.MENU)
        self.button.set_image(img)
        self.button.set_tooltip_text(_('Point.im commands'))
        actions_hbox.pack_start(self.button, False, False , 0)
        actions_hbox.reorder_child(self.button,
            len(actions_hbox.get_children()) - 3)
        id_ = self.button.connect('clicked', self.on_pointim_button_clicked)
        self.chat_control.handlers[id_] = self.button
        self.button.show()
        # create pointim tag button
        self.tag_button = Gtk.Button(label=None, stock=None, use_underline=True)
        self.tag_button.set_property('relief', Gtk.ReliefStyle.NONE)
        self.tag_button.set_property('can-focus', False)
        img = Gtk.Image()
        img_path = self.plugin.local_file_path('pointim_tag_button.png')
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(img_path)
        iconset = Gtk.IconSet(pixbuf=pixbuf)
        factory.add('pointim_tag', iconset)
        factory.add_default()
        img.set_from_icon_name('pointim_tag', Gtk.IconSize.MENU)
        self.tag_button.set_image(img)
        actions_hbox.pack_start(self.tag_button, False, False , 0)
        actions_hbox.reorder_child(self.tag_button,
            len(actions_hbox.get_children()) - 4)
        id_ = self.tag_button.connect('clicked', self.on_pointim_tag_button_clicked)
        self.chat_control.handlers[id_] = self.tag_button
        self.tag_button.set_no_show_all(True)
        self.tag_button.set_tooltip_text(_('Point.im tags'))
        self.tag_button.set_property('visible', self.plugin.config[
            'SHOW_TAG_BUTTON'])

    def create_link_menu(self):
        """
        Create pointim link context menu
        """
        self.pointim_link_menu = Gtk.Menu()

        item = Gtk.MenuItem.new_with_mnemonic(_('Reply to message'))
        item.connect('activate', self.on_reply)
        self.pointim_link_menu.append(item)

        menuitems = ((_('Unsubscribe from comments'), 'U #WORD'),
                     (_('Subscribe to message replies'), 'S #WORD'),
                     (_('Recommend post'), '! #WORD'),
                     (_('Show message with replies'), '#WORD+'),
                     (_('Delete post'), 'D #WORD'),)
        for menuitem in menuitems:
            item = Gtk.MenuItem.new_with_mnemonic(menuitem[0])
            item.connect('activate', self.send, menuitem[1])
            self.pointim_link_menu.append(item)

        item = Gtk.MenuItem.new_with_mnemonic(_('Open in browser'))
        item.connect('activate', self.open_in_browser)
        self.pointim_link_menu.append(item)

        menuitems = ((_('Show user\'s info'), 'NICK'),
                     (_('Show user\'s info and last 10 messages'), 'NICK+'),
                     (_('Subscribe to user\'s blog'), 'S NICK'),
                     (_('Unsubscribe from user\'s blog'), 'U NICK'),
                     (_('Add/delete user to/from your blacklist'), 'BL NICK'),)
        for menuitem in menuitems:
            item = Gtk.MenuItem.new_with_mnemonic(menuitem[0])
            item.connect('activate', self.send, menuitem[1])
            self.pointim_link_menu.append(item)

        item = Gtk.MenuItem.new_with_mnemonic(_('Send personal message'))
        item.connect('activate', self.on_pm)
        self.pointim_link_menu.append(item)

    def create_tag_menu(self):
        """
        Create pointim tag button menu
        """
        self.menu = Gtk.Menu()
        for num in range(1, 11):
            menuitem = self.plugin.config['MENUITEM' + str(num)]
            text = self.plugin.config['MENUITEM_TEXT' + str(num)]
            if not menuitem or not text:
                continue
            item = Gtk.MenuItem.new_with_mnemonic(menuitem)
            item.connect('activate', self.on_insert, text)
            self.menu.append(item)
        self.menu.show_all()

    def pointim_hyperlink_handler(self, texttag, widget, event, iter_, kind):
        # handle message links( #12345 or #12345/6) and pointim nicks
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            # show popup menu (right mouse button clicked)
            begin_iter = iter_.copy()
            # we get the begining of the tag
            while not begin_iter.begins_tag(texttag):
                begin_iter.backward_char()
            end_iter = iter_.copy()
            # we get the end of the tag
            while not end_iter.ends_tag(texttag):
                end_iter.forward_char()

            buffer_ = self.textview.tv.get_buffer()
            word = buffer_.get_text(begin_iter, end_iter, True)
            self.pointim_post = word

            post = self.pointim_post_re.search(word)
            nick = self.pointim_nick_re.search(word)
            if post is None and nick is None:
                return
            childs = self.pointim_link_menu.get_children()
            if post:
                self.pointim_post_full = app.interface.pointim_sharp_slash_re\
                                                    .search(word).group(0)
                self.pointim_post_uid = post.group(1)
                for menuitem in range(7):
                    childs[menuitem].show()
                for menuitem in range(7, 13):
                    childs[menuitem].hide()
            if nick:
                self.pointim_nick = nick.group(0)
                for menuitem in range(7):
                    childs[menuitem].hide()
                for menuitem in range(7, 13):
                    childs[menuitem].show()
            self.pointim_link_menu.popup(None, None, None,
                event.button.button, event.time)
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            # insert message num or nick (left mouse button clicked)
            begin_iter = iter_.copy()
            # we get the begining of the tag
            while not begin_iter.begins_tag(texttag):
                begin_iter.backward_char()
            end_iter = iter_.copy()
            # we get the end of the tag
            while not end_iter.ends_tag(texttag):
                end_iter.forward_char()
            buffer_ = self.textview.tv.get_buffer()
            word = buffer_.get_text(begin_iter, end_iter, True)
            if kind == 'pointim_sharp_slash':
                self.on_insert(widget, word)
            if kind == 'pointim_nick':
                self.on_insert(widget, 'PM %s' % word.rstrip(':'))

    def print_special_text(self, special_text, other_tags, graphics=True):
        if app.interface.pointim_sharp_slash_re.match(special_text):
            # insert post num #123456//
            buffer_, iter_, tag = self.get_iter_and_tag('pointim_sharp_slash')
            mark = buffer_.create_mark(None, iter_, True)
            iter_ = buffer_.get_iter_at_mark(mark)
            print('#########3', special_text)
            buffer_.insert_with_tags(iter_, special_text, tag)

            self.last_pointim_num = special_text
            self.textview.plugin_modified = False
            return
        if app.interface.pointim_nick_re.match(special_text):
            return
            # insert pointim nick @nickname////
            buffer_, iter_, tag = self.get_iter_and_tag('pointim_nick')
            mark = buffer_.create_mark(None, iter_, True)
            nick = special_text[1:].rstrip(':')
            buffer_.insert_with_tags(iter_, special_text, tag)
            # insert avatars
            if not self.plugin.config['SHOW_AVATARS']:
                self.textview.plugin_modified = True
                return
            b_nick = buffer_.get_text(buffer_.get_start_iter(),
                buffer_.get_iter_at_mark(mark),False)
            if self.plugin.config['ONLY_AUTHOR_AVATAR'] and not \
            special_text.endswith(':') and b_nick[-9:] not in ('Subscribed to '
            ):
                self.textview.plugin_modified = True
                return
            if self.plugin.config['ONLY_FIRST_AVATAR']:
                if b_nick[-9:] not in ('Reply by ', 'message from ', 'ended by ',
                'Subscribed to '):
                    if b_nick[-2] != app.config.get('after_nickname'):
                        self.textview.plugin_modified = True
                        return
                    elif b_nick[-1] == '\n':
                        self.textview.plugin_modified = True
                        return
            conn = app.connections[self.chat_control.account]
            if not conn.connected:
                self.textview.plugin_modified = True
                return
            # search id in the db
            query = "select nick, id from person where nick = :nick"
            self.plugin.cursor.execute(query, {'nick':nick})
            db_item = self.plugin.cursor.fetchone()
            if db_item:
                # nick in the db
                pixbuf = self.get_avatar(db_item[1], nick, True)
                if not pixbuf:
                    self.textview.plugin_modified = True
                    return
                end_iter = buffer_.get_iter_at_mark(mark)
                anchor = buffer_.create_child_anchor(end_iter)
                img = TextViewImage(anchor, nick)
                img.set_from_pixbuf(pixbuf)
                img.show()
                self.textview.tv.add_child_at_anchor(img, anchor)
                self.textview.plugin_modified = True
                return
            else:
                # nick not in the db
                GLib.idle_add(self.get_new_avatar, mark, nick)
                return
        if app.interface.pointim_pic_re.match(special_text) and \
            self.plugin.config['SHOW_PREVIEW']:
            return
            # show pics preview
            buffer_, iter_, tag = self.get_iter_and_tag('url')
            mark = buffer_.create_mark(None, iter_, True)
            buffer_.insert_with_tags(iter_, special_text, tag)
            uid = special_text.split('/')[-1]
            url = "http://i.pointim.com/photos-512/%s" % uid
            app.thread_interface(self.insert_pic_preview, [mark, special_text,
                url])
            self.textview.plugin_modified = True
            return

    def insert_pic_preview(self, mark, special_text, url):
        pixbuf = self.get_pixbuf_from_url( url, self.plugin.config[
            'PREVIEW_SIZE'])
        if pixbuf:
            # insert image
            buffer_ = mark.get_buffer()
            end_iter = buffer_.get_iter_at_mark(mark)
            anchor = buffer_.create_child_anchor(end_iter)
            img = TextViewImage(anchor, special_text)
            img.set_from_pixbuf(pixbuf)
            img.show()
            self.textview.tv.add_child_at_anchor(img, anchor)

    def get_iter_and_tag(self, tag_name):
        buffer_ = self.textview.tv.get_buffer()
        ttable = buffer_.get_tag_table()
        tag = ttable.lookup(tag_name)
        return buffer_, buffer_.get_end_iter(), tag

    def get_new_avatar(self, mark, nick):
        try:
            response = urlopen('http://api.pointim.com/users?uname=%s' % nick)
            j = json.load(response)
            _id = str(j[0]['uid'])
        except Exception as e:
            return
        buffer_ = mark.get_buffer()
        end_iter = buffer_.get_iter_at_mark(mark)
        pixbuf = self.get_avatar(_id, nick)
        anchor = buffer_.create_child_anchor(end_iter)
        img = TextViewImage(anchor, nick)
        img.set_from_pixbuf(pixbuf)
        img.show()
        self.textview.tv.add_child_at_anchor(img, anchor)



    def get_avatar(self, uid, nick, need_check=None):
        # search avatar in cache or download from pointim.com
        pic = uid + '.png'
        pic_path = os.path.join(self.plugin.cache_path, pic)
        #pic_path = pic_path.decode(locale.getpreferredencoding())
        url = 'http://api.pointim.com/avatar?uname=%s&size=32' % nick
        if need_check and os.path.isfile(pic_path):
            max_old = self.plugin.config['avatars_old']
            if (time.time() - os.stat(pic_path).st_mtime) < max_old:
                return GdkPixbuf.Pixbuf.new_from_file(pic_path)

        avatar_size = self.plugin.config['AVATAR_SIZE']
        pixbuf = self.get_pixbuf_from_url(url, avatar_size)
        if pixbuf:
             # save to cache
            pixbuf.savev(pic_path, 'png', [], [])
            if need_check:
                return pixbuf
            query = "select nick, id from person where nick = :nick"
            self.plugin.cursor.execute(query, {'nick':nick})
            db_item = self.plugin.cursor.fetchone()
            if not db_item:
                data = (nick, uid)
                self.plugin.cursor.execute('insert into person(nick, id)'
                    ' values (?, ?)', data)
                self.plugin.conn.commit()
        else:
            img_path = self.plugin.local_file_path('unknown.png')
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(img_path)
            pixbuf, w, h = self.get_pixbuf_of_size(pixbuf, avatar_size)
        return pixbuf

    def get_pixbuf_from_url(self, url, size):
        # download avatar and resize him
        try:
            data, alt = helpers.download_image(self.textview.account,
                {'src': url})
            pix = GdkPixbuf.PixbufLoader()
            if data:
                pix.write(data)
                pix.close()
                pixbuf = pix.get_pixbuf()
                pixbuf, w, h = self.get_pixbuf_of_size(pixbuf, size)
            else:
                pix.close()
                return
        except Exception as e:
            pix.close()
            return
        return pixbuf

    def get_pixbuf_of_size(self, pixbuf, size):
        # Creates a pixbuf that fits in the specified square of sizexsize
        # while preserving the aspect ratio
        # Returns tuple: (scaled_pixbuf, actual_width, actual_height)
        image_width = pixbuf.get_width()
        image_height = pixbuf.get_height()

        if image_width > image_height:
            if image_width > size:
                image_height = int(size / float(image_width) * image_height)
                image_width = int(size)
        else:
            if image_height > size:
                image_width = int(size / float(image_height) * image_width)
                image_height = int(size)

        crop_pixbuf = pixbuf.scale_simple(image_width, image_height,
            GdkPixbuf.InterpType.BILINEAR)
        return (crop_pixbuf, image_width, image_height)

    def on_textview_button_press_event(self, widget, event, obj):
        obj.selected_phrase = ''

        if event.button != 3:
            return False

        x, y = obj.tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT,
            int(event.x), int(event.y))
        iter_ = obj.tv.get_iter_at_location(x, y)
        tags = iter_.get_tags()

        if tags:
            for tag in tags:
                tag_name = tag.get_property('name')
                if tag_name in ('pointim_nick', 'pointim_sharp_slash'):
                    return True

        self.textview.on_textview_button_press_event(widget, event)

    def on_textview_motion_notify_event(self, widget, event):
        # Change the cursor to a hand when we are over a nicks or an post nums
        pointer_x, pointer_y = self.textview.tv.get_window(
            Gtk.TextWindowType.TEXT).get_pointer()[1:3]
        x, y = self.textview.tv.window_to_buffer_coords(Gtk.TextWindowType.TEXT,
            pointer_x, pointer_y)
        if Gtk.MINOR_VERSION > 18:
            iter_ = self.textview.tv.get_iter_at_location(x, y)[1]
        else:
            iter_ = self.textview.tv.get_iter_at_location(x, y)[0]
        tags = iter_.get_tags()
        tag_table = self.textview.tv.get_buffer().get_tag_table()
        if self.change_cursor:
            self.textview.tv.get_window(Gtk.TextWindowType.TEXT).set_cursor(
                    Gdk.Cursor.new(Gdk.CursorType.XTERM))
            self.change_cursor = False
        for tag in tags:
            if tag in (self.textview.tagSharpSlash, self.textview.tagPointimNick):
                self.textview.tv.get_window(Gtk.TextWindowType.TEXT).set_cursor(
                    Gdk.Cursor.new(Gdk.CursorType.HAND2))
            self.change_cursor = True
        #self.textview.on_textview_motion_notify_event(widget, event)

    def on_pointim_button_clicked(self, widget):
        """
        Popup pointim menu
        """
        menu = Gtk.Menu()
        menuitems = ((_('Show last messages from public timeline'), '#+'),
                     (_('Show last messages from your feed'), '#'),
                     (_('Show popular personal blogs'), '@'),
                     (_('Show your tags'), '*'),
                     (_('Show your subscriptions'), 'S'),
                     (_('Delete last message'), 'D LAST'),
                     (_('Enable subscriptions delivery'), 'ON'),
                     (_('Disable subscriptions delivery'), 'OFF'),
                     (_('Show your blacklist'), 'BL'),
                     (_('Update "About" info from Jabber vCard'), 'VCARD'),
                     (_('Ping'), 'PING'),
                     (_('Login'), 'LOGIN'),
                     (_('HELP'), 'HELP'),)
        for menuitem in menuitems:
            item = Gtk.MenuItem.new_with_mnemonic(menuitem[0])
            item.connect('activate', self.send, menuitem[1])
            menu.append(item)

        menu.show_all()
        gtkgui_helpers.popup_emoticons_under_button(menu, widget,
                self.chat_control.parent_win)

    def on_pointim_tag_button_clicked(self, widget):
        gtkgui_helpers.popup_emoticons_under_button(self.menu, widget,
                                                self.chat_control.parent_win)

    def send(self, widget, text):
        msg = text.replace('WORD', self.pointim_post_uid).replace(
            'NICK', self.pointim_nick.rstrip(':'))
        self.chat_control.send_message(msg)
        self.chat_control.msg_textview.grab_focus()

    def on_insert(self, widget, text):
        """
        Insert text to conversation input box, at cursor position
        """
        text = text.rstrip() + ' '
        message_buffer = self.chat_control.msg_textview.get_buffer()
        message_buffer.insert_at_cursor(text)
        self.chat_control.msg_textview.grab_focus()

    def on_reply(self, widget):
        self.on_insert(widget, self.pointim_post_full)

    def on_pm(self, widget):
        self.on_insert(widget, 'PM %s' % self.pointim_nick.rstrip(':'))

    def open_in_browser(self, widget):
        post = self.pointim_post_comment_re.search(self.pointim_post)
        url = None
        if post is not None:
            url = 'https://point.im/%s#%s' % (post.group(1), post.group(2))
        else:
            post = self.pointim_post_re.search(self.pointim_post)
            if post is not None:
                url = 'https://point.im/%s' % post.group(1)
        if url is not None:
            helpers.launch_browser_mailer('url', url)

    def disconnect_from_chat_control(self):
        buffer_ = self.textview.tv.get_buffer()
        tag_table = buffer_.get_tag_table()
        if tag_table.lookup('pointim_sharp_slash'):
            tag_table.remove(self.textview.tagSharpSlash)
            tag_table.remove(self.textview.tagPointimNick)
            tag_table.remove(self.textview.tagPointimPic)
        actions_hbox = self.chat_control.xml.get_object('actions_hbox')
        actions_hbox.remove(self.button)
        actions_hbox.remove(self.tag_button)

    def mykeypress_event(self, widget, event):
        if event.keyval == Gdk.KEY_Up:
            if event.state & Gdk.ModifierType.MOD1_MASK:  # Alt+UP
                self.on_insert(widget, self.last_pointim_num)
                return True


class PointimPluginConfigDialog(GajimPluginConfigDialog):
    def init(self):
        self.GTK_BUILDER_FILE_PATH = self.plugin.local_file_path(
            'config_dialog.ui')
        self.xml = Gtk.Builder()
        self.xml.set_translation_domain('gajim_plugins')
        self.xml.add_objects_from_file(self.GTK_BUILDER_FILE_PATH, ['vbox1'])
        self.checkbutton = self.xml.get_object('checkbutton')
        self.only_first_avatar = self.xml.get_object('only_first_avatar')
        self.avatar_size_spinbutton = self.xml.get_object('avatar_size')
        self.avatar_size_spinbutton.get_adjustment().configure(20, 10, 32, 1, 10, 0)
        self.avatars_old = self.xml.get_object('avatars_old')
        self.avatars_old.get_adjustment().configure(20, 1, 3650, 1, 10, 0)
        self.show_pic = self.xml.get_object('show_pic')
        self.preview_size_spinbutton = self.xml.get_object('preview_size')
        self.preview_size_spinbutton.get_adjustment().configure(20, 10, 512, 1, 10, 0)

        self.link_colorbutton = self.xml.get_object('link_colorbutton')
        vbox = self.xml.get_object('vbox1')
        self.get_child().pack_start(vbox, True, True, 0)

        self.xml.connect_signals(self)

    def on_run(self):
        self.checkbutton.set_active(self.plugin.config['SHOW_AVATARS'])
        self.only_first_avatar.set_active(self.plugin.config[
            'ONLY_FIRST_AVATAR'])
        self.xml.get_object('only_author_avatar').set_active(
                                    self.plugin.config['ONLY_AUTHOR_AVATAR'])
        self.avatar_size_spinbutton.set_value(self.plugin.config['AVATAR_SIZE'])
        self.avatars_old.set_value(self.plugin.config['avatars_old'] / 86400)
        self.show_pic.set_active(self.plugin.config['SHOW_PREVIEW'])
        self.preview_size_spinbutton.set_value(self.plugin.config[
            'PREVIEW_SIZE'])
        self.link_colorbutton.set_color(Gdk.color_parse(
            self.plugin.config['LINK_COLOR']))
        self.xml.get_object('show_tag_button').set_active(self.plugin.config[
            'SHOW_TAG_BUTTON'])
        for num in range(1, 11):
            self.xml.get_object('menuitem' + str(num)).set_text(
                self.plugin.config['MENUITEM' + str(num)])
            self.xml.get_object('menuitem_text' + str(num)).set_text(
                self.plugin.config['MENUITEM_TEXT' + str(num)])

    def on_checkbutton_toggled(self, checkbutton):
        self.plugin.config['SHOW_AVATARS'] = checkbutton.get_active()

    def on_only_author_ava_toggled(self, checkbutton):
        self.plugin.config['ONLY_AUTHOR_AVATAR'] = checkbutton.get_active()

    def on_only_first_avatar_toggled(self, checkbutton):
        self.plugin.config['ONLY_FIRST_AVATAR'] = checkbutton.get_active()

    def avatar_size_value_changed(self, spinbutton):
        self.plugin.config['AVATAR_SIZE'] = spinbutton.get_value()

    def on_avatars_old_value_changed(self, spinbutton):
        self.plugin.config['avatars_old'] = spinbutton.get_value() * 86400

    def on_show_pic_toggled(self, checkbutton):
        self.plugin.config['SHOW_PREVIEW'] = checkbutton.get_active()

    def on_show_tag_button_toggled(self, checkbutton):
        self.plugin.config['SHOW_TAG_BUTTON'] = checkbutton.get_active()
        for control in self.plugin.controls:
            control.tag_button.set_property('visible', checkbutton.get_active())

    def preview_size_value_changed(self, spinbutton):
        self.plugin.config['PREVIEW_SIZE'] = spinbutton.get_value()

    def on_link_colorbutton_color_set(self, colorbutton):
        color = colorbutton.get_color().to_string()
        self.plugin.config['LINK_COLOR'] = color
        for control in self.plugin.controls:
            control.textview.tagSharpSlash.set_property('foreground', color)
            control.textview.tagPointimNick.set_property('foreground', color)

    def menuitem_changed(self, widget):
        name = (Gtk.Buildable.get_name(widget)).upper()
        self.plugin.config[name] = widget.get_text()
        for control in self.plugin.controls:
            control.create_tag_menu()
