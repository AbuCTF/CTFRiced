CTFd._internal.challenge.data = window.challenge || {}

CTFd._internal.challenge.preRender = function () {}

CTFd._internal.challenge.render = function (markdown) {
    return markdown
}

CTFd._internal.challenge.postRender = function () {
    function ordinalize(i) {
        var j = i % 10,
            k = i % 100;
        if (j == 1 && k != 11) return i + "st";
        if (j == 2 && k != 12) return i + "nd";
        if (j == 3 && k != 13) return i + "rd";
        return i + "th";
    }

    function getSolves(id) {
        return CTFd.api.get_challenge_solves({ challengeId: id }).then(response => {
            const first_blood_bonus = CTFd._internal.challenge.data.first_blood_bonus;
            const data = response.data;

            // Update solve count for core-beta theme
            const solveButton = document.querySelector(".challenge-solves");
            if (solveButton) {
                solveButton.textContent = `${data.length} Solve${data.length !== 1 ? 's' : ''}`;
            }

            const box = document.querySelector("#challenge-solves-names");
            if (box) {
                box.innerHTML = '';
                for (let i = 0; i < data.length; i++) {
                    const name = data[i].name;
                    const date = typeof dayjs !== 'undefined'
                        ? dayjs(data[i].date).fromNow()
                        : moment(data[i].date).fromNow();
                    const account_url = data[i].account_url;

                    const tr = document.createElement('tr');
                    const td1 = document.createElement('td');
                    td1.style.width = '10%';

                    if (i < first_blood_bonus.length) {
                        let text = `<b>${ordinalize(i + 1)}</b>`;
                        if (first_blood_bonus[i])
                            text += ` (+${first_blood_bonus[i]})`;
                        if (i < 3)
                            text = `<span class="award-icon award-medal-${ordinalize(i + 1)}"></span>` + text;
                        else
                            text = `<span class="award-icon award-medal"></span>` + text;
                        td1.innerHTML = text;
                    }

                    const td2 = document.createElement('td');
                    td2.innerHTML = `<a href="${account_url}">${name}</a>`;
                    
                    const td3 = document.createElement('td');
                    td3.textContent = date;

                    tr.appendChild(td1);
                    tr.appendChild(td2);
                    tr.appendChild(td3);
                    box.appendChild(tr);
                }
            }
        });
    }

    // Setup event listener for solve tab click (compatible with core-beta)
    const solvesButton = document.querySelector(".challenge-solves");
    if (solvesButton) {
        solvesButton.addEventListener('click', function (event) {
            event.preventDefault();
            
            // Update table header
            const solvesHeader = document.querySelector("#solves thead");
            if (solvesHeader) {
                solvesHeader.innerHTML = '<tr><td></td><td><b>Name</b></td><td><b>Date</b></td></tr>';
            }
            
            // Get challenge ID and load solves
            const challengeIdInput = document.querySelector("#challenge-id");
            if (challengeIdInput) {
                getSolves(challengeIdInput.value);
            }
        });
    }
}

CTFd._internal.challenge.submit = function (preview) {
    var challenge_id = parseInt(document.querySelector("#challenge-id").value)
    var submission = document.querySelector("#challenge-input").value

    var body = {
        challenge_id,
        submission,
    }
    var params = {}
    if (preview) params.preview = true

    return CTFd.api.post_challenge_attempt(params, body).then(response => {
        return response
    })
}
