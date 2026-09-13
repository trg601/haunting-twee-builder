//Play a memory video, automatically moving to specified passage after it ends.
setup.playMemory = function (filepath, passage) {
  $(document).one(":passagedisplay", function (event) {
    let video = document.createElement("video");
    video.setAttribute("src", filepath);
    video.setAttribute("id", "memory");
    let passages = document.getElementsByClassName("passage");
    let uibar = document.getElementById("ui-bar");
    if (!video || !uibar || !passages || !passages.length) {
      console.log("Video not found!");
      return;
    }
    passages[0].appendChild(video);
    // Create listener to end video and show UI bar
    let startVideo = function () {
      let uiStyle = uibar.style.display;
      uibar.style.display = "none";
      video.addEventListener("ended", function () {
        uibar.style.display = uiStyle;
        Engine.play(passage);
      });
    };
    // Play video
    video
      .play()
      .then(startVideo)
      .catch((error) => {
        // Autoplay was prevented
        console.log("Autoplay was blocked:", error.name);
        video.muted = true;
        video
          .play()
          .then(startVideo)
          .catch(() => {
            console.log("Unable to play memory, skipping...");
            Engine.play(passage);
          });
      });
  });
};

//progress to specified passage after a click or a timeout
setup.waitForClick = function (passage, timeout = 10) {
  var timeoutId = null;
  let reset = function () {
    $("html").off("click");
    $("#history-backward").off("click", reset);
    clearTimeout(timeoutId);
    SimpleAudio.stop();
  };
  let moveToPassage = function () {
    reset();
    Engine.play(passage);
  };

  timeoutId = setTimeout(() => moveToPassage(), timeout * 1000);
  $("html").on("click", function (ev) {
    //Add callback for click event
    if (ev.target == $("html")[0]) moveToPassage();
  });

  $("#history-backward").on("click", reset);
};

//Button that briefly shows different text after click
//Must be used within <<link>> macro
//Example: <<link "Passage">><<run setup.buttonMirage("Passage", "altText")>><</link>>
setup.buttonMirage = function (passage, altText) {
  var links = document.getElementsByClassName("link-internal");
  if (!links) return;
  for (let i = 0; i < links.length; i++) {
    let link = links[i];
    if (link.innerHTML == passage) {
      link.innerHTML = altText;
      setTimeout(function () {
        Engine.play(passage);
      }, 200);
    }
  }
};

//play a list of sounds in order
setup.playSounds = function () {
  SimpleAudio.lists.clear();
  SimpleAudio.lists.add("notes", ...arguments);
  let playList = SimpleAudio.lists.get("notes");
  // patch onEnd function because it is stupid and throws an error if you go back in story
  playList._onEnd = function () {
    if (typeof this.queue == "undefined" || 0 === this.queue.length) {
      if (!this._loop) return;
      this._fillQueue();
    }
    this._next() && this.current.track.play();
  };
  playList.playWhenAllowed();
};

//play notes based on current resistance levels
setup.playNotes = function () {
  let resist = State.variables.resist;
  let soundPrefix = resist > 0 ? "resist" : "play";
  let sounds = [];
  for (let i = 0; i < State.variables.loop_counter; i++)
    sounds.push({
      id: soundPrefix + "1",
      own: true, // make this a copy for duplicates
    });
  sounds.push("murmur1");
  setup.playSounds(sounds);
};

//log current variable status
setup.logVariables = function () {
  let vars = State.variables;
  console.log(`Loop: ${vars.loop_counter}
Resist meter: ${vars.resist}
Total plays: ${vars.total_play}, total resists: ${vars.total_resist}`);
};
