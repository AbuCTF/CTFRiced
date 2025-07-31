CTFd.plugin.run((_CTFd) => {
    const $ = _CTFd.lib.$
    const md = _CTFd.lib.markdown()
    $('a[href="#new-desc-preview"]').on('shown.bs.tab', function (event) {
        if (event.target.hash == '#new-desc-preview') {
            var editor_value = $('#new-desc-editor').val();
            $(event.target.hash).html(
                md.render(editor_value)
            );
        }
    });
    $(document).ready(function(){
    $('[data-toggle="tooltip"]').tooltip();
    $.getJSON("/api/v1/docker", function(result){
        if (result.success) {
            // Group images by server
            const serverGroups = {};
            
            $.each(result['data'], function(i, item){
                if (item.error) {
                    // Add error items directly
                    $("#dockerimage_select").append(
                        $("<option />").val(item.name).text(item.name).attr('disabled', true)
                    );
                } else {
                    // Group by server
                    if (!serverGroups[item.server_name]) {
                        serverGroups[item.server_name] = [];
                    }
                    serverGroups[item.server_name].push(item);
                }
            });
            
            // Add grouped options
            Object.keys(serverGroups).sort().forEach(function(serverName) {
                // Add server group header
                const optgroup = $("<optgroup />").attr('label', serverName);
                
                serverGroups[serverName].forEach(function(item) {
                    optgroup.append(
                        $("<option />")
                            .val(item.name)
                            .text(item.image_name)
                            .attr('data-server-id', item.server_id)
                            .attr('data-server-name', item.server_name)
                            .attr('data-image-name', item.image_name)
                    );
                });
                
                $("#dockerimage_select").append(optgroup);
            });
            
            // If no servers available, show error
            if (Object.keys(serverGroups).length === 0 && $("#dockerimage_select option").length === 0) {
                document.docker_form.dockerimage_select.disabled = true;
                $("label[for='DockerImage']").text('Docker Image - No servers configured!')
            }
        } else {
            // Handle error case
            document.docker_form.dockerimage_select.disabled = true;
            $("label[for='DockerImage']").text('Docker Image - Error loading servers!')
            $("#dockerimage_select").append($("<option />").val('ERROR').text(result.data[0].name || 'Error loading Docker servers').attr('disabled', true));
        }
    }).fail(function() {
        // Handle AJAX failure
        document.docker_form.dockerimage_select.disabled = true;
        $("label[for='DockerImage']").text('Docker Image - Connection Error!')
        $("#dockerimage_select").append($("<option />").val('ERROR').text('Failed to connect to Docker API').attr('disabled', true));
    });
});
});