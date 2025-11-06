import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.webdriver import WebDriver

def sign_in(wd : WebDriver) -> WebDriver:
    sign = wd

    #加载账号密码及网址
    with open("data/account.json", "r", encoding="utf-8") as f:
        account = json.load(f)

    sign.get("about:blank")
    sign.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
        // 拦截 addEventListener
        const nativeAdd = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = function(type, fn, opts) {
            if (type === 'mouseout') {
                console.log('[block] mouseout on', this);
                return;   // 直接丢弃
            }
            nativeAdd.call(this, type, fn, opts);
        };

        // 拦截 onmouseout 属性
        Object.defineProperty(Element.prototype, 'onmouseout', {
            set: function(_) { /* 什么都不做 */ },
            get: function() { return null; }
        });
    """
    })

    #访问网址（尽量填写未完成的小节的视频页的网址）
    course = str(account["course"])
    sign.get(course)

    #设置等待时间
    sign.implicitly_wait(10)

    #输入账号
    phone = str(account["phone"])
    phone_wd = sign.find_element(By.CLASS_NAME, "ipt-tel")
    phone_wd.send_keys(phone)

    #输入密码
    password = str(account["password"])
    password_wd = sign.find_element(By.CLASS_NAME, "ipt-pwd")
    password_wd.send_keys(password)

    #点击登录
    sign_in_button = sign.find_element(By.TAG_NAME, "button")
    sign_in_button.click()

    return sign