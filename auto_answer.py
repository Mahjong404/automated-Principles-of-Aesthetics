import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.webdriver import WebDriver

def answer(wd : WebDriver) -> WebDriver:
    choose = wd
    choose.switch_to.frame('iframe')
    choose.find_element(By.TAG_NAME, "iframe")

    with open("data/questions.json", "r", encoding="utf-8") as f:
        questions = json.load(f)

    choose.find_element(By.CSS_SELECTOR, "li[qtype='1']").click()
    return choose