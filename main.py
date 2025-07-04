import re
import asyncio
import time
from typing import Optional, Tuple, Dict, Union, List
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, MessageMediaType, ChatType, ChatMemberStatus
from pyrogram.errors import (
    FloodWait, RPCError, MessageIdInvalid, ChannelInvalid,
    ChatAdminRequired, PeerIdInvalid, UserNotParticipant, BadRequest
)

# ====================== CONFIGURATION ======================
API_ID = 20219694
API_HASH = "29d9b3a01721ab452fcae79346769e29"
BOT_TOKEN = "7717087225:AAECM3YBnsPYst0tG2_nGi7uqhDhTostrWM"

class Config:
    OFFSET = 0
    PROCESSING = False
    BATCH_MODE = False
    SOURCE_CHAT = None
    TARGET_CHAT = None
    START_ID = None
    END_ID = None
    CURRENT_TASK = None
    REPLACEMENTS = {}
    ADMIN_CACHE = {}
    MESSAGE_FILTERS = {
        'text': True,
        'photo': True,
        'video': True,
        'document': True,
        'audio': True,
        'animation': True,
        'voice': True,
        'video_note': True,
        'sticker': True,
        'poll': True,
        'contact': True
    }
    MAX_RETRIES = 3
    DELAY_BETWEEN_MESSAGES = 0.3
    MAX_MESSAGES_PER_BATCH = 1900
    REMOVE_TELEGRAM_URLS = False  # New config for URL removal

app = Client(
    "ultimate_batch_link_modifier",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ====================== UTILITY FUNCTIONS ======================
def is_not_command(_, __, message: Message) -> bool:
    return not message.text.startswith('/')

def parse_message_link(text: str) -> Optional[Tuple[Union[int, str], int]]:
    """Parse Telegram message link and return (chat_id, message_id) tuple"""
    pattern = r'(?:https?://)?(?:t\.me|telegram\.(?:me|dog))/(?:c/)?([^/\s]+)/(\d+)'
    match = re.search(pattern, text)
    if match:
        chat_id = match.group(1)
        message_id = int(match.group(2))
        return (chat_id, message_id)
    return None

def modify_content(text: str, offset: int) -> str:
    if not text:
        return text

    # Apply word replacements
    for original, replacement in sorted(Config.REPLACEMENTS.items(), key=lambda x: (-len(x[0]), x[0].lower())):
        text = re.sub(rf'\b{re.escape(original)}\b', replacement, text, flags=re.IGNORECASE)

    # Remove Telegram URLs if enabled
    if Config.REMOVE_TELEGRAM_URLS:
        pattern = r'(https?://)?(t\.me|telegram\.(?:me|dog))/(c/)?([^/\s]+)/(\d+)'
        text = re.sub(pattern, '', text).strip()
    # Modify Telegram links (if URL removal is not enabled)
    elif offset != 0:
        def replacer(match):
            prefix = match.group(1) or ""
            domain = match.group(2)
            chat_part = match.group(3) or ""
            chat_id = match.group(4)
            post_id = match.group(5)
            return f"{prefix}{domain}/{chat_part}{chat_id}/{int(post_id) + offset}"

        pattern = r'(https?://)?(t\.me|telegram\.(?:me|dog))/(c/)?([^/\s]+)/(\d+)'
        text = re.sub(pattern, replacer, text)

    return text

async def verify_permissions(client: Client, chat_id: Union[int, str]) -> Tuple[bool, str]:
    try:
        if isinstance(chat_id, str):
            chat = await client.get_chat(chat_id)
            chat_id = chat.id

        if chat_id in Config.ADMIN_CACHE:
            return Config.ADMIN_CACHE[chat_id]

        chat = await client.get_chat(chat_id)
        
        if chat.type not in [ChatType.CHANNEL, ChatType.SUPERGROUP]:
            result = (False, "Only channels and supergroups are supported")
            Config.ADMIN_CACHE[chat_id] = result
            return result
            
        try:
            member = await client.get_chat_member(chat.id, "me")
        except UserNotParticipant:
            result = (False, "Bot is not a member of this chat")
            Config.ADMIN_CACHE[chat_id] = result
            return result
            
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            result = (False, "Bot needs to be admin")
            Config.ADMIN_CACHE[chat_id] = result
            return result
        
        required_perms = ["can_post_messages", "can_delete_messages"] if chat.type == ChatType.CHANNEL else ["can_send_messages"]
        
        if member.privileges:
            missing_perms = [
                perm for perm in required_perms 
                if not getattr(member.privileges, perm, False)
            ]
            if missing_perms:
                result = (False, f"Missing permissions: {', '.join(missing_perms)}")
                Config.ADMIN_CACHE[chat_id] = result
                return result
        
        result = (True, "OK")
        Config.ADMIN_CACHE[chat_id] = result
        return result
        
    except (ChannelInvalid, PeerIdInvalid):
        return (False, "Invalid chat ID")
    except Exception as e:
        return (False, f"Error: {str(e)}")

async def process_message(client: Client, source_msg: Message, target_chat_id: int) -> bool:
    for attempt in range(Config.MAX_RETRIES):
        try:
            if source_msg.service or source_msg.empty:
                return False
                
            media_type = source_msg.media
            if media_type and Config.MESSAGE_FILTERS.get(media_type.value, False):
                caption = source_msg.caption or ""
                modified_caption = modify_content(caption, Config.OFFSET)
                
                media_mapping = {
                    MessageMediaType.PHOTO: client.send_photo,
                    MessageMediaType.VIDEO: client.send_video,
                    MessageMediaType.DOCUMENT: client.send_document,
                    MessageMediaType.AUDIO: client.send_audio,
                    MessageMediaType.ANIMATION: client.send_animation,
                    MessageMediaType.VOICE: client.send_voice,
                    MessageMediaType.VIDEO_NOTE: client.send_video_note,
                    MessageMediaType.STICKER: client.send_sticker
                }
                
                if media_type in media_mapping:
                    kwargs = {
                        'chat_id': target_chat_id,
                        'caption': modified_caption if media_type != MessageMediaType.STICKER else None,
                        'parse_mode': ParseMode.MARKDOWN
                    }
                    kwargs[media_type.value] = getattr(source_msg, media_type.value).file_id
                    
                    await media_mapping[media_type](**kwargs)
                    return True
                else:
                    await client.copy_message(
                        chat_id=target_chat_id,
                        from_chat_id=source_msg.chat.id,
                        message_id=source_msg.id,
                        caption=modified_caption,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return True
            elif source_msg.text and Config.MESSAGE_FILTERS['text']:
                await client.send_message(
                    chat_id=target_chat_id,
                    text=modify_content(source_msg.text, Config.OFFSET),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=source_msg.reply_markup
                )
                return True
                
            return False
            
        except FloodWait as e:
            if attempt == Config.MAX_RETRIES - 1:
                raise
            await asyncio.sleep(e.value)
        except Exception as e:
            print(f"Attempt {attempt + 1} failed for message {source_msg.id}: {e}")
            if attempt == Config.MAX_RETRIES - 1:
                return False
            await asyncio.sleep(1)
    
    return False

# ====================== COMMAND HANDLERS ======================
@app.on_message(filters.command(["start", "help"]))
async def start_cmd(client: Client, message: Message):
    help_text = """
🚀 **Ultimate Batch Link Modifier Bot** �

🔹 **Core Features:**
- Batch process messages with ID offset
- Smart word replacement system
- Comprehensive media support
- Automatic retry mechanism
- Telegram URL removal option

🔹 **Basic Commands:**
/setchat source [chat] - Set source chat
/setchat target [chat] - Set target chat
/batch - Start batch processing
/addnumber N - Add offset N
/lessnumber N - Subtract offset N
/setoffset N - Set absolute offset
/stop - Cancel current operation

🔹 **URL Removal Commands:**
/removeurls on - Enable URL removal
/removeurls off - Disable URL removal
/removeurls status - Check current status

🔹 **Advanced Commands:**
/replacewords - View replacements
/addreplace ORIG REPL - Add replacement
/removereplace WORD - Remove replacement
/filtertypes - Show filters
/enablefilter TYPE - Enable filter
/disablefilter TYPE - Disable filter

🔹 **System Commands:**
/status - Show current config
/reset - Reset all settings
"""
    await message.reply(help_text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command(["addnumber", "addnum"]))
async def add_offset(client: Client, message: Message):
    try:
        offset = int(message.command[1])
        Config.OFFSET += offset
        await message.reply(f"✅ Offset increased by {offset}. New offset: {Config.OFFSET}")
    except (IndexError, ValueError):
        await message.reply("❌ Please provide a valid number to add")

@app.on_message(filters.command(["lessnumber", "lessnum"]))
async def subtract_offset(client: Client, message: Message):
    try:
        offset = int(message.command[1])
        Config.OFFSET -= offset
        await message.reply(f"✅ Offset decreased by {offset}. New offset: {Config.OFFSET}")
    except (IndexError, ValueError):
        await message.reply("❌ Please provide a valid number to subtract")

@app.on_message(filters.command("setoffset"))
async def set_offset(client: Client, message: Message):
    try:
        offset = int(message.command[1])
        Config.OFFSET = offset
        await message.reply(f"✅ Offset set to {Config.OFFSET}")
    except (IndexError, ValueError):
        await message.reply("❌ Please provide a valid offset number")

@app.on_message(filters.command("replacewords"))
async def show_replacements(client: Client, message: Message):
    if not Config.REPLACEMENTS:
        await message.reply("ℹ️ No word replacements set")
        return
    
    replacements_text = "🔹 Current Word Replacements:\n"
    for original, replacement in Config.REPLACEMENTS.items():
        replacements_text += f"▫️ `{original}` → `{replacement}`\n"
    
    await message.reply(replacements_text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command("addreplace"))
async def add_replacement(client: Client, message: Message):
    try:
        original = message.command[1]
        replacement = message.command[2]
        Config.REPLACEMENTS[original] = replacement
        await message.reply(f"✅ Added replacement: `{original}` → `{replacement}`", parse_mode=ParseMode.MARKDOWN)
    except IndexError:
        await message.reply("❌ Usage: /addreplace ORIGINAL REPLACEMENT")

@app.on_message(filters.command("removereplace"))
async def remove_replacement(client: Client, message: Message):
    try:
        word = message.command[1]
        if word in Config.REPLACEMENTS:
            del Config.REPLACEMENTS[word]
            await message.reply(f"✅ Removed replacement for `{word}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply(f"❌ No replacement found for `{word}`", parse_mode=ParseMode.MARKDOWN)
    except IndexError:
        await message.reply("❌ Please specify a word to remove")

@app.on_message(filters.command("filtertypes"))
async def show_filters(client: Client, message: Message):
    filters_text = "🔹 Current Message Filters:\n"
    for filter_type, enabled in Config.MESSAGE_FILTERS.items():
        status = "✅ Enabled" if enabled else "❌ Disabled"
        filters_text += f"▫️ {filter_type}: {status}\n"
    
    await message.reply(filters_text)

@app.on_message(filters.command("enablefilter"))
async def enable_filter(client: Client, message: Message):
    try:
        filter_type = message.command[1].lower()
        if filter_type in Config.MESSAGE_FILTERS:
            Config.MESSAGE_FILTERS[filter_type] = True
            await message.reply(f"✅ Enabled {filter_type} messages")
        else:
            await message.reply(f"❌ Invalid filter type. Available types: {', '.join(Config.MESSAGE_FILTERS.keys())}")
    except IndexError:
        await message.reply("❌ Please specify a filter type to enable")

@app.on_message(filters.command("disablefilter"))
async def disable_filter(client: Client, message: Message):
    try:
        filter_type = message.command[1].lower()
        if filter_type in Config.MESSAGE_FILTERS:
            Config.MESSAGE_FILTERS[filter_type] = False
            await message.reply(f"✅ Disabled {filter_type} messages")
        else:
            await message.reply(f"❌ Invalid filter type. Available types: {', '.join(Config.MESSAGE_FILTERS.keys())}")
    except IndexError:
        await message.reply("❌ Please specify a filter type to disable")

@app.on_message(filters.command("removeurls"))
async def toggle_url_removal(client: Client, message: Message):
    try:
        action = message.command[1].lower()
        if action == "on":
            Config.REMOVE_TELEGRAM_URLS = True
            await message.reply("✅ Telegram URL removal is now ENABLED")
        elif action == "off":
            Config.REMOVE_TELEGRAM_URLS = False
            await message.reply("✅ Telegram URL removal is now DISABLED")
        elif action == "status":
            status = "ENABLED" if Config.REMOVE_TELEGRAM_URLS else "DISABLED"
            await message.reply(f"ℹ️ Telegram URL removal is currently {status}")
        else:
            await message.reply("❌ Usage: /removeurls [on|off|status]")
    except IndexError:
        await message.reply("❌ Usage: /removeurls [on|off|status]")

@app.on_message(filters.command("status"))
async def show_status(client: Client, message: Message):
    status_text = f"""
🔹 **Current Configuration**
▫️ Offset: {Config.OFFSET}
▫️ Replacements: {len(Config.REPLACEMENTS)}
▫️ Processing: {'✅ Yes' if Config.PROCESSING else '❌ No'}
▫️ Batch Mode: {'✅ Yes' if Config.BATCH_MODE else '❌ No'}
▫️ Message Filters: {sum(Config.MESSAGE_FILTERS.values())}/{len(Config.MESSAGE_FILTERS)} enabled
▫️ URL Removal: {'✅ Enabled' if Config.REMOVE_TELEGRAM_URLS else '❌ Disabled'}
"""
    if Config.SOURCE_CHAT:
        status_text += f"▫️ Source Chat: {Config.SOURCE_CHAT.title} (ID: {Config.SOURCE_CHAT.id})\n"
    else:
        status_text += "▫️ Source Chat: Not set\n"
    
    if Config.TARGET_CHAT:
        status_text += f"▫️ Target Chat: {Config.TARGET_CHAT.title} (ID: {Config.TARGET_CHAT.id})"
    else:
        status_text += "▫️ Target Chat: Not set (will use current chat)"
    
    await message.reply(status_text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command("reset"))
async def reset_config(client: Client, message: Message):
    Config.OFFSET = 0
    Config.REPLACEMENTS = {}
    Config.PROCESSING = False
    Config.BATCH_MODE = False
    Config.SOURCE_CHAT = None
    Config.TARGET_CHAT = None
    Config.START_ID = None
    Config.END_ID = None
    Config.MESSAGE_FILTERS = {k: True for k in Config.MESSAGE_FILTERS}
    Config.REMOVE_TELEGRAM_URLS = False
    
    if Config.CURRENT_TASK:
        Config.CURRENT_TASK.cancel()
        Config.CURRENT_TASK = None
    
    await message.reply("✅ All settings have been reset to defaults")

@app.on_message(filters.command(["setchat", "setgroup"]))
async def set_chat(client: Client, message: Message):
    try:
        if len(message.command) < 2:
            return await message.reply("Usage: /setchat [source|target] [chat_id or username]")
        
        chat_type = message.command[1].lower()
        if chat_type not in ["source", "target"]:
            return await message.reply("Invalid type. Use 'source' or 'target'")
        
        if message.reply_to_message:
            chat = message.reply_to_message.chat
        elif len(message.command) > 2:
            chat_id = message.command[2]
            try:
                chat = await client.get_chat(chat_id)
            except Exception as e:
                return await message.reply(f"Invalid chat: {str(e)}")
        else:
            chat = message.chat
        
        has_perms, perm_msg = await verify_permissions(client, chat.id)
        if not has_perms:
            return await message.reply(f"Permission error: {perm_msg}")
        
        if chat_type == "source":
            Config.SOURCE_CHAT = chat
        else:
            Config.TARGET_CHAT = chat
        
        await message.reply(
            f"✅ {'Source' if chat_type == 'source' else 'Target'} chat set to:\n"
            f"Title: {chat.title}\n"
            f"ID: {chat.id}\n"
            f"Username: @{chat.username if chat.username else 'N/A'}"
        )
    except Exception as e:
        await message.reply(f"Error: {str(e)}")

@app.on_message(filters.command(["showchat", "showgroup"]))
async def show_chat(client: Client, message: Message):
    text = "🔹 Current Chat Settings:\n"
    if Config.SOURCE_CHAT:
        text += (
            f"▫️ Source: {Config.SOURCE_CHAT.title}\n"
            f"ID: {Config.SOURCE_CHAT.id}\n"
            f"Username: @{Config.SOURCE_CHAT.username if Config.SOURCE_CHAT.username else 'N/A'}\n\n"
        )
    else:
        text += "▫️ Source: Not set\n\n"
    
    if Config.TARGET_CHAT:
        text += (
            f"▫️ Target: {Config.TARGET_CHAT.title}\n"
            f"ID: {Config.TARGET_CHAT.id}\n"
            f"Username: @{Config.TARGET_CHAT.username if Config.TARGET_CHAT.username else 'N/A'}\n"
        )
    else:
        text += "▫️ Target: Not set (will use current chat)\n"
    
    await message.reply(text)

@app.on_message(filters.command("clearchat"))
async def clear_chat(client: Client, message: Message):
    try:
        if len(message.command) < 2:
            return await message.reply("Usage: /clearchat [source|target|all]")
        
        chat_type = message.command[1].lower()
        if chat_type == "source":
            Config.SOURCE_CHAT = None
            await message.reply("✅ Source chat cleared")
        elif chat_type == "target":
            Config.TARGET_CHAT = None
            await message.reply("✅ Target chat cleared")
        elif chat_type == "all":
            Config.SOURCE_CHAT = None
            Config.TARGET_CHAT = None
            await message.reply("✅ Both source and target chats cleared")
        else:
            await message.reply("Invalid type. Use 'source', 'target' or 'all'")
    except Exception as e:
        await message.reply(f"Error: {str(e)}")

@app.on_message(filters.command("batch"))
async def start_batch(client: Client, message: Message):
    if Config.PROCESSING:
        return await message.reply("⚠️ Already processing! Use /stop to cancel")
    
    if not Config.SOURCE_CHAT:
        return await message.reply("❌ Source chat not set. Use /setchat source [chat_id]")
    
    Config.PROCESSING = True
    Config.BATCH_MODE = True
    Config.START_ID = None
    Config.END_ID = None
    
    await message.reply(
        f"🔹 **Batch Mode Activated**\n"
        f"▫️ Source: {Config.SOURCE_CHAT.title}\n"
        f"▫️ Target: {Config.TARGET_CHAT.title if Config.TARGET_CHAT else 'Current Chat'}\n"
        f"▫️ Offset: {Config.OFFSET}\n"
        f"▫️ Replacements: {len(Config.REPLACEMENTS)}\n"
        f"▫️ URL Removal: {'✅ ON' if Config.REMOVE_TELEGRAM_URLS else '❌ OFF'}\n\n"
        f"Reply to the FIRST message or send its link"
    )

@app.on_message(filters.command(["stop", "cancel"]))
async def stop_cmd(client: Client, message: Message):
    if Config.PROCESSING:
        Config.PROCESSING = False
        if Config.CURRENT_TASK:
            Config.CURRENT_TASK.cancel()
            Config.CURRENT_TASK = None
        await message.reply("✅ Processing stopped")
    else:
        await message.reply("⚠️ No active process")

@app.on_message(filters.text & filters.create(is_not_command))
async def handle_message(client: Client, message: Message):
    if not Config.PROCESSING:
        return
    
    try:
        # Get source message details
        if message.reply_to_message:
            source_msg = message.reply_to_message
            chat_id = source_msg.chat.id
            msg_id = source_msg.id
        else:
            link_info = parse_message_link(message.text)
            if not link_info:
                return await message.reply("❌ Invalid message link")
            
            chat_identifier, msg_id = link_info
            
            # Resolve the chat properly
            try:
                chat = await client.get_chat(chat_identifier)
                chat_id = chat.id
            except Exception as e:
                return await message.reply(f"❌ Could not resolve chat: {str(e)}")

        if Config.BATCH_MODE:
            if Config.START_ID is None:
                # First message of batch
                has_perms, perm_msg = await verify_permissions(client, chat_id)
                if not has_perms:
                    Config.PROCESSING = False
                    return await message.reply(f"❌ Permission error: {perm_msg}")
                
                # Verify this is same chat as source chat
                if Config.SOURCE_CHAT and chat_id != Config.SOURCE_CHAT.id:
                    return await message.reply("❌ First message must be from the source chat")
                
                Config.START_ID = msg_id
                await message.reply(
                    f"✅ First message set: {msg_id}\n"
                    f"Now reply to the LAST message or send its link"
                )
            elif Config.END_ID is None:
                # Second message of batch
                if not Config.SOURCE_CHAT:
                    Config.PROCESSING = False
                    return await message.reply("❌ Source chat not set")
                
                # Verify same chat as source
                if chat_id != Config.SOURCE_CHAT.id:
                    return await message.reply("❌ Last message must be from the same chat as source chat")
                
                Config.END_ID = msg_id
                Config.CURRENT_TASK = asyncio.create_task(process_batch(client, message))
        else:
            # Single message processing
            try:
                msg = await client.get_messages(chat_id, msg_id)
                if msg and not msg.empty:
                    target_chat = Config.TARGET_CHAT.id if Config.TARGET_CHAT else message.chat.id
                    success = await process_message(client, msg, target_chat)
                    if not success:
                        await message.reply("⚠️ Failed to process this message")
            except Exception as e:
                await message.reply(f"❌ Error: {str(e)}")
            
    except Exception as e:
        await message.reply(f"❌ Critical error: {str(e)}")
        Config.PROCESSING = False
        Config.BATCH_MODE = False

async def process_batch(client: Client, message: Message):
    try:
        if not Config.SOURCE_CHAT:
            await message.reply("❌ Source chat not set")
            Config.PROCESSING = False
            return
            
        start_id = min(Config.START_ID, Config.END_ID)
        end_id = max(Config.START_ID, Config.END_ID)
        total = end_id - start_id + 1
        
        if total > Config.MAX_MESSAGES_PER_BATCH:
            await message.reply(f"❌ Batch too large ({total} messages). Max allowed: {Config.MAX_MESSAGES_PER_BATCH}")
            Config.PROCESSING = False
            return
            
        target_chat = Config.TARGET_CHAT.id if Config.TARGET_CHAT else message.chat.id
        
        # Verify permissions
        has_perms, perm_msg = await verify_permissions(client, Config.SOURCE_CHAT.id)
        if not has_perms:
            await message.reply(f"❌ Source chat permission error: {perm_msg}")
            Config.PROCESSING = False
            return
            
        has_perms, perm_msg = await verify_permissions(client, target_chat)
        if not has_perms:
            await message.reply(f"❌ Target chat permission error: {perm_msg}")
            Config.PROCESSING = False
            return

        progress_msg = await message.reply(
            f"⚡ **Batch Processing Started**\n"
            f"▫️ Source: {Config.SOURCE_CHAT.title}\n"
            f"▫️ Target: {Config.TARGET_CHAT.title if Config.TARGET_CHAT else message.chat.title}\n"
            f"▫️ Range: {start_id}-{end_id}\n"
            f"▫️ Total: {total} messages\n"
            f"▫️ Offset: {Config.OFFSET}\n"
            f"▫️ URL Removal: {'✅ ON' if Config.REMOVE_TELEGRAM_URLS else '❌ OFF'}\n",
            parse_mode=ParseMode.MARKDOWN
        )
        
        processed = failed = 0
        last_update = time.time()
        
        for current_id in range(start_id, end_id + 1):
            if not Config.PROCESSING:
                break
            
            try:
                msg = await client.get_messages(Config.SOURCE_CHAT.id, current_id)
                if msg and not msg.empty:
                    success = await process_message(client, msg, target_chat)
                    if success:
                        processed += 1
                    else:
                        failed += 1
                else:
                    failed += 1
                
                if time.time() - last_update >= 5 or current_id == end_id:
                    progress = ((current_id - start_id) / total) * 100
                    try:
                        await progress_msg.edit(
                            f"⚡ **Processing Batch**\n"
                            f"▫️ Progress: {progress:.1f}%\n"
                            f"▫️ Current: {current_id}\n"
                            f"✅ Success: {processed}\n"
                            f"❌ Failed: {failed}"
                        )
                        last_update = time.time()
                    except:
                        pass
                
                await asyncio.sleep(Config.DELAY_BETWEEN_MESSAGES)
            except FloodWait as e:
                await progress_msg.edit(f"⏳ Flood wait: {e.value}s...")
                await asyncio.sleep(e.value)
            except Exception as e:
                print(f"Error processing {current_id}: {e}")
                failed += 1
                await asyncio.sleep(1)
        
        if Config.PROCESSING:
            await progress_msg.edit(
                f"✅ **Batch Complete!**\n"
                f"▫️ Total: {total}\n"
                f"✅ Success: {processed}\n"
                f"❌ Failed: {failed}\n"
                f"▫️ Success Rate: {(processed/total)*100:.1f}%"
            )
    
    except Exception as e:
        await message.reply(f"❌ Batch failed: {str(e)}")
    finally:
        Config.PROCESSING = False
        Config.BATCH_MODE = False
        Config.CURRENT_TASK = None

if __name__ == "__main__":
    print("⚡ Ultimate Batch Link Modifier Bot Started!")
    try:
        app.start()
        idle()
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        app.stop()
