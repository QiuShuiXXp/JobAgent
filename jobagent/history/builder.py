from jobagent.domain.messages import FocusedObject, MessageType, UserMessage, Turn, BotMessage

# 卡片类型在两头的 payload 不一样，只能按类型分派：
#   job_list     后端发 {"jobs":[{id,title,company,salary,city,score}...]}
#                前端回 {"job_selection":[job_id...]}
#   resume       后端发整份摘要 {"name","experiences","education","skills",...}
#                前端回 {"filename":...}
#   login_window 后端发 {"status":"waiting"|"logged_in"}
#                前端回 {"login_confirmed":"已登录"}（键名就是槽位名）
#   task_report  后端发 {"phase":"created","total":n,"dry_run":bool}
LOGIN_STATUS_TEXT = {"waiting": "系统给出了登录窗口，等用户在左侧完成 BOSS 登录",
                     "logged_in": "BOSS 登录已完成"}
JOB_LIST_KEEP = 5     # 一轮 15 条岗位全写进历史会挤爆规划器窗口，只留前几条
SKILL_KEEP = 6


class ChatHistoryBuilder:
    """
    构建历史会话
    """
    @staticmethod
    def build(turns: list[Turn]):
        chat_messages=[]

        for turn in turns:
            # 获取用户历史消息
            user_message=turn.user_message
            # 用户消息可能是卡片对象或者是文本
            user_message_str=ChatHistoryBuilder.process_user_message(user_message=user_message)
            if user_message_str:
                chat_messages.append(f"USER:{user_message_str}")

            # 获取机器人回复消息
            bot_messages =turn.bot_messages
            for bot_message in bot_messages:
                bot_message_str=ChatHistoryBuilder.process_bot_message(bot_message=bot_message)
                if bot_message_str:
                    chat_messages.append(f"BOT:{bot_message_str}")
        return "\n".join(chat_messages)

    @staticmethod
    def process_user_message(user_message:UserMessage)->str:
        """
        处理用户消息
        :param user_message:
        :return:
        """
        if user_message.type is MessageType.TEXT:
            return (user_message.text or "").strip()
        # 否则为卡片对象
        return ChatHistoryBuilder._reader_obj_msg(object=user_message.object)

    @staticmethod
    def _reader_obj_msg(object:FocusedObject|None)->str:
        """
        消息为卡片对象
        :param object:
        :return:
        """
        # 一律返回非空：调用方拼的是 f"BOT:{...}"，空串会在历史里留下半句话
        if object is None:
            return ""
        payload = object.payload or {}
        if object.type == "job_list":
            return ChatHistoryBuilder._job_list_text(payload)
        if object.type == "resume":
            return ChatHistoryBuilder._resume_text(payload)
        if object.type == "login_window":
            return ChatHistoryBuilder._login_text(payload)
        if object.type == "task_report":
            total = payload.get("total")
            if payload.get("phase") == "created":
                dry = "（演练模式，不真的发消息）" if payload.get("dry_run") else ""
                return f"[系统创建了逐岗位沟通任务，共 {total} 个{dry}]"
            return f"[逐岗位沟通任务进展：{payload.get('phase', '未知')}]"
        # 兜底：前端将来加新卡片类型时，历史里至少留下"用户点了个什么卡"
        return f"[{object.type}]"

    @staticmethod
    def _job_list_text(payload:dict)->str:
        """岗位卡：后端那张列岗位，前端那张只带勾选的 id。"""
        jobs = payload.get("jobs") or []
        if jobs:
            head = "、".join(
                f"{j.get('company') or ''}/{j.get('title') or ''}"
                f"（{j.get('salary') or '薪资未标'}，{j.get('score') or '-'}分）"
                for j in jobs[:JOB_LIST_KEEP])
            more = f" 等共 {len(jobs)} 个" if len(jobs) > JOB_LIST_KEEP else ""
            return f"[系统给出候选岗位：{head}{more}]"
        # 勾选回传里只有 encryptJobId，历史表里没有 id→岗位 的映射，说不出是哪个
        selected = payload.get("job_selection") or []
        if selected:
            return f"[用户勾选了 {len(selected)} 个岗位]"
        return "[系统的岗位列表为空]"

    @staticmethod
    def _resume_text(payload:dict)->str:
        if not payload.get("name"):
            # 前端回传只有文件名；后端那张卡姓名也可能被 LLM 抽成 null，两种都归这一句
            return f"[用户上传了简历：{payload.get('filename') or '未识别出姓名'}]"
        bits = []
        bits.append(f"{payload['name']}")
        if payload.get("expected_position"):
            bits.append(f"期望岗位 {payload['expected_position']}")
        if payload.get("expected_salary"):
            bits.append(f"期望薪资 {payload['expected_salary']}")
        skills = [str(s) for s in (payload.get("skills") or [])][:SKILL_KEEP]
        if skills:
            bits.append("技能：" + "、".join(skills))
        experiences = payload.get("experiences") or []
        if experiences:
            first = experiences[0] or {}
            bits.append(f"最近一份：{first.get('company') or ''}{first.get('title') or ''}")
            bits.append(f"{len(experiences)} 段工作经历")
        education = payload.get("education") or []
        if education and (education[0] or {}).get("school"):
            e = education[0]
            bits.append(f"学历：{e.get('school')}{e.get('degree') or ''}")
        return "[简历摘要：" + "，".join(bits) + "]"

    @staticmethod
    def _login_text(payload:dict)->str:
        if payload.get("login_confirmed"):
            return f"[用户回复：{payload['login_confirmed']}]"
        status = payload.get("status")
        return f"[{LOGIN_STATUS_TEXT.get(status, f'登录窗口状态：{status}') or '登录窗口'}]"

    @staticmethod
    def process_bot_message(bot_message:BotMessage)->str:
        """
        处理机器人回复的消息
        :param bot_message:
        :return:
        """
        if bot_message.text:
            return bot_message.text.strip()
        return ChatHistoryBuilder._reader_obj_msg(object=bot_message.object)


