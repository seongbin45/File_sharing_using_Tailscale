# 동봉한 외부 파일

`app/static/vendor/` 의 파일들은 npm 에서 받아 **그대로** 넣은 것입니다. 수정하지 마십시오.

| 파일 | 패키지 | 버전 | 라이선스 |
|---|---|---|---|
| `xterm.js`, `xterm.css` | [xterm](https://www.npmjs.com/package/xterm) | 5.3.0 | MIT (`LICENSE.xterm`) |
| `xterm-addon-fit.js` | [xterm-addon-fit](https://www.npmjs.com/package/xterm-addon-fit) | 0.8.0 | MIT |

## 왜 CDN 을 쓰지 않는가

처음에는 cdnjs 에서 불러왔습니다. 그렇게 하면 **터미널 탭 전체가 남의 서버에 의존**합니다.

- 이 콘솔이 도는 PC 에 인터넷 경로가 없을 수 있습니다. tailnet 만 있으면 나머지 기능은
  전부 되는데 터미널만 죽습니다.
- CDN 이 죽거나 차단되면 `Terminal is not defined` 만 콘솔에 찍히고 화면은 빈 채로 남습니다.
  실제로 개발 중에 이 상태를 봤습니다.
- 백업 시스템을 고치러 들어가는 화면이 외부 가용성에 묶이는 것은 앞뒤가 맞지 않습니다.

파일 세 개, 290 KB 입니다. 저장소에 넣는 편이 낫습니다.

## 갱신 방법

```bash
npm pack xterm@<버전> xterm-addon-fit@<버전>
tar xzf xterm-<버전>.tgz
cp package/lib/xterm.js  app/static/vendor/xterm.js
cp package/css/xterm.css app/static/vendor/xterm.css
cp package/LICENSE       app/static/vendor/LICENSE.xterm
# xterm-addon-fit 도 같은 방식으로 package/lib/xterm-addon-fit.js
```

버전을 올렸으면 위 표도 같이 고치고, `python -m tests.selftest` 와 브라우저에서
터미널 탭을 실제로 열어 확인하십시오.
