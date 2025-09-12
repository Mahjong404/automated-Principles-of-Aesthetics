import os
import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains

import auto_video
import auto_answer

#开始设置
options = webdriver.EdgeOptions()
options.add_experimental_option('excludeSwitches', ['enable-logging'])

#调用驱动启动浏览器
wd = webdriver.Edge(options=options, service=Service(r".\msedgedriver.exe"))

#访问网址（尽量填写未完成的小节的视频页的网址）
wd.get("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=0&courseId=0&clazzid=0&cpi=0&enc=0&mooc2=1&openc=7c41ee4a5471afaa01bc474eeebee3cc")

#设置等待时间
wd.implicitly_wait(10)

#加载账号密码
with open("data/account.json", "r", encoding="utf-8") as f:
    account = json.load(f)

#输入账号
phone = str(account["phone"])
phone_wd = wd.find_element(By.CLASS_NAME, "ipt-tel")
phone_wd.send_keys(phone)

#输入密码
password = str(account["password"])
password_wd = wd.find_element(By.CLASS_NAME, "ipt-pwd")
password_wd.send_keys(password)

#登录
sign_in_button = wd.find_element(By.TAG_NAME, "button")
sign_in_button.click()

#观看视频
wd = auto_video.video(wd)

#点击“下一节”按钮，跳转到习题部分
wd.switch_to.default_content()
wd.find_element(By.ID, "prevNextFocusNext").click()

#自动做题
wd = auto_answer.answer(wd)

input("按回车退出")

wd.quit()